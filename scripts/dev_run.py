"""并行启动前后端开发服务器的跨平台脚本。

`make run` 原先以 `run: run-be run-fe` 顺序执行两个目标，而 `run-be`
启动的是阻塞式开发服务器，永远不会返回，导致 `run-fe` 无法执行。

本脚本用子进程同时拉起后端（Django runserver）与前端（Vite）开发服务器，
统一转发两者输出并带上前缀，任一进程退出或收到 Ctrl+C 时优雅关闭全部进程。
不依赖具体 shell（cmd/bash/PowerShell 均可），兼容 Windows/Linux/macOS。

进程树清理策略：
- Windows：将子进程加入配置了 KILL_ON_JOB_CLOSE 的 Job 对象，关闭 Job 句柄即
  原子式终止整棵进程树，规避 Django 自动重载子进程被杀后又被重启导致的孤儿进程。
- POSIX：子进程独立成会话，向其进程组发送 SIGINT 即可级联终止全部派生进程。

启动前端口清理：
- 后端固定监听 8000，前端默认 5173（Vite 占用时自动递增到 5174/5175）。
  上次会话异常退出（如 IDE 强制结束）时这些端口常被残留进程占用，导致新一次
  `make run` 启动后端时立即报 "That port is already in use"。
  启动前主动清理 8000/5173/5174/5175 端口上的残留进程，避免每次手动 kill。
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

# 项目根目录（scripts 的上一级）
ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"

_IS_WINDOWS = sys.platform == "win32"

# 统一以 UTF-8 处理所有输出：中文 Windows 默认 locale 为 GBK，若用文本模式管道
# 或直接写入 GBK 编码的 stdout，会在解码/编码 UTF-8 内容（Vite 箭头、Django
# 中文、本脚本的中文提示）时抛出 UnicodeDecodeError/UnicodeEncodeError。
_OUTPUT_ENCODING = "utf-8"

# Windows 上 npm 需要通过 npm.cmd 调用；作为 PATH 解析失败时的兜底命令名
NPM = "npm.cmd" if _IS_WINDOWS else "npm"

# 启动前需清理的开发端口：
# - 8000：后端 Django runserver 固定端口，被占用会直接报错退出
# - 5173/5174/5175：前端 Vite 默认端口及被占用时的自动递增端口
#   清理后让本次启动拿到干净的 5173，避免 Vite 越漂越远
_DEV_PORTS = (8000, 5173, 5174, 5175)

# 端口清理时等待进程退出的超时（秒）：先 SIGTERM 优雅退出，超时后 SIGKILL
_PORT_RELEASE_TIMEOUT = 6


def _log(message: str) -> None:
    """以 UTF-8 字节写入 stdout，避免 GBK 控制台编码中文提示时报错。"""
    data = message.encode(_OUTPUT_ENCODING, errors="replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        # 极少数无 buffer 的场景退化为 errors=replace 的文本写入
        sys.stdout.write(message)
        sys.stdout.flush()


def _find_port_pids(port: int) -> list[int]:
    """返回监听指定端口的进程 PID 列表（排除本进程）。

    跨平台策略：
    - POSIX：优先 ``lsof -ti :{port}``（直接输出 PID），回退 ``ss -tlnpH`` 解析
      ``users:(("proc",pid=N,fd=N))`` 字段。两者均不可用则返回空。
    - Windows：解析 ``netstat -ano`` 输出，匹配 ``:{port}`` 监听行的 PID 列。
    """
    if _IS_WINDOWS:
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            # 行格式：TCP  0.0.0.0:8000  0.0.0.0:0  LISTENING  1234
            if len(parts) >= 5 and parts[0] in ("TCP", "TCPv6") and parts[-2] == "LISTENING":
                local = parts[1]
                if local.rsplit(":", 1)[-1] == str(port):
                    with contextlib.suppress(ValueError):
                        pids.append(int(parts[-1]))
        return [p for p in set(pids) if p != os.getpid()]

    # POSIX：依次尝试 lsof / ss，第一个有结果即返回
    for cmd, parser in (
        (["lsof", "-ti", f":{port}"], _parse_lsof_pids),
        (["ss", "-tlnpH", f"sport = :{port}"], _parse_ss_pids),
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        pids = parser(result.stdout)
        if pids:
            return [p for p in set(pids) if p != os.getpid()]
    return []


def _parse_lsof_pids(stdout: str) -> list[int]:
    """``lsof -ti :{port}`` 直接输出 PID（每行一个）。"""
    pids: list[int] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _parse_ss_pids(stdout: str) -> list[int]:
    """解析 ``ss -tlnpH`` 输出中的 ``users:(("proc",pid=N,fd=N))`` 字段。"""
    pids: list[int] = []
    pid_re = re.compile(r"pid=(\d+)")
    for line in stdout.splitlines():
        m = pid_re.search(line)
        if m:
            pids.append(int(m.group(1)))
    return pids


def _terminate_pid(pid: int, *, force: bool) -> None:
    """终止指定进程：POSIX 用 SIGTERM/SIGKILL，Windows 用 taskkill。"""
    sig = signal.SIGKILL if force else signal.SIGTERM
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if _IS_WINDOWS:
            # /T 连同子进程一起终止；/F 强制（force=True 时）
            args = ["taskkill", "/PID", str(pid), "/T"]
            if force:
                args.append("/F")
            subprocess.run(args, check=False, timeout=10)
        else:
            os.kill(pid, sig)


def _cleanup_stale_processes() -> None:
    """启动前清理占用开发端口的残留进程。

    针对场景：上次 ``make run`` 异常退出（如关闭终端窗口、IDE 强制结束）后，
    Django/Vite 子进程仍在监听端口，导致本次启动后端立即报端口占用。

    清理流程（每个端口独立处理）：
    1. 探测占用 PID 列表；
    2. 先 SIGTERM（Windows taskkill 不带 /F）优雅退出，等待最多
       ``_PORT_RELEASE_TIMEOUT`` 秒；
    3. 仍占用则 SIGKILL（Windows taskkill /F）强制结束；
    4. 再次探测确认端口已释放；仍失败则告警但不阻塞启动（交由后续 Popen 报错）。
    """
    for port in _DEV_PORTS:
        pids = _find_port_pids(port)
        if not pids:
            continue
        pid_str = ", ".join(str(p) for p in pids)
        _log(f"[dev_run] 端口 {port} 被占用（PID: {pid_str}），正在清理...\n")

        # 第一阶段：SIGTERM 优雅退出
        for pid in pids:
            _terminate_pid(pid, force=False)

        # 等待端口释放
        deadline = time.monotonic() + _PORT_RELEASE_TIMEOUT
        while time.monotonic() < deadline:
            if not _find_port_pids(port):
                break
            time.sleep(0.3)

        # 第二阶段：仍未释放则 SIGKILL 强制结束
        remaining = _find_port_pids(port)
        if remaining:
            _log(f"[dev_run] 端口 {port} 仍未释放，强制终止 PID: {', '.join(map(str, remaining))}\n")
            for pid in remaining:
                _terminate_pid(pid, force=True)
            # 短暂等待内核回收
            time.sleep(0.5)

        # 最终确认
        final = _find_port_pids(port)
        if final:
            _log(
                f"[dev_run] 警告：端口 {port} 仍被占用（PID: {', '.join(map(str, final))}），启动可能失败，请手动处理\n"
            )
        else:
            _log(f"[dev_run] 端口 {port} 已释放\n")


def _resolve_frontend() -> tuple[str, list[str]]:
    """返回前端启动命令，优先使用 bun，未安装时回退到 npm。

    用 shutil.which 解析可执行文件全路径，避免 Windows 上 bun.exe 与 npm.cmd
    后缀差异导致 subprocess.Popen 找不到可执行文件。bun 启动更快且自带运行时，
    但非必需；npm 随 Node.js 自带，作为兜底保证仅装了 Node 的环境也能运行。
    """
    bun_path = shutil.which("bun")
    if bun_path:
        return "bun", [bun_path, "run", "dev"]
    npm_path = shutil.which("npm")
    if npm_path:
        return "npm", [npm_path, "run", "dev"]
    # PATH 解析失败时退回原始命令名，让子进程报错便于排查
    return "npm", [NPM, "run", "dev"]


# 前端包管理器解析在模块加载时一次性完成（运行期间环境不变）
_FRONTEND_RUNNER, _FRONTEND_CMD = _resolve_frontend()

# 各服务的启动命令与工作目录
SERVICES: list[tuple[str, list[str], Path]] = [
    (
        "backend",
        ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"],
        BACKEND_DIR,
    ),
    (
        "frontend",
        _FRONTEND_CMD,
        FRONTEND_DIR,
    ),
]


def _child_env() -> dict[str, str]:
    """构造子进程环境，强制 Python 子进程以 UTF-8 输出。

    中文 Windows 默认 locale 为 GBK，Python 子进程写入管道时会用 GBK 编码，
    与本脚本按 UTF-8 解码不一致导致乱码或错误。设置 PYTHONUTF8/PYTHONIOENCODING
    让后端 Python 子进程统一输出 UTF-8，与前端 Node（本就输出 UTF-8）保持一致。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _create_win_job() -> int | None:
    """创建一个「随句柄关闭而终止全部成员」的 Windows Job 对象，返回其句柄。

    非 Windows 平台返回 None。Job 配置 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 后，
    只要主进程关闭该句柄（或主进程退出释放句柄），Job 内所有进程（含孙进程）
    都会被内核一并终止，从而彻底避免自动重载子进程被重启后的孤儿残留。
    """
    if not _IS_WINDOWS:
        return None

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION 结构，仅需设置 LimitFlags
    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9  # JobObjectExtendedLimitInformation
    limit_kill_on_job_close = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        job_object_extended_limit_information,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    return job


def _assign_to_job(job: int | None, pid: int) -> None:
    """将指定进程加入 Job 对象（仅 Windows）。"""
    if job is None or not _IS_WINDOWS:
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_set_quota = 0x0100
    process_terminate = 0x0001
    handle = kernel32.OpenProcess(process_set_quota | process_terminate, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _close_job(job: int | None) -> None:
    """关闭 Job 句柄，触发内核终止 Job 内全部进程（仅 Windows）。"""
    if job is None or not _IS_WINDOWS:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(wintypes.HANDLE(job))


def _stream_output(name: str, stream: IO[bytes]) -> None:
    """逐行读取子进程二进制输出，按 UTF-8 解码并加服务名前缀转发到 stdout。

    子进程（Vite/Django）以 UTF-8 输出，这里显式按 UTF-8 解码并对非法字节
    容错替换，避免在 GBK locale 下用文本模式管道解码时抛出 UnicodeDecodeError。
    """
    prefix = f"[{name}] "
    for raw in iter(stream.readline, b""):
        line = raw.decode(_OUTPUT_ENCODING, errors="replace")
        _log(prefix + line)
    stream.close()


def _install_signal_handlers() -> None:
    """将中断信号统一转为 KeyboardInterrupt，确保 finally 清理逻辑执行。

    Windows 下 Ctrl+Break 发送 SIGBREAK，Python 默认直接终止进程而不抛出
    KeyboardInterrupt，会跳过 finally 清理导致子进程残留。这里显式注册处理器。
    """

    def _raise_interrupt(signum: int, frame: object) -> None:  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _raise_interrupt)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _raise_interrupt)


def main() -> int:
    """并行启动全部服务，阻塞直至任一服务退出或收到中断信号。"""
    _install_signal_handlers()
    # 启动前清理上次会话残留的进程，避免端口占用导致后端启动失败
    _cleanup_stale_processes()
    job = _create_win_job()
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    threads: list[threading.Thread] = []

    for name, command, cwd in SERVICES:
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=_child_env(),
                # 以二进制模式读取管道，由 _stream_output 按 UTF-8 解码，
                # 规避 GBK locale 下文本模式管道的 UnicodeDecodeError
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Windows 下新建进程组，避免控制台 Ctrl+C 直接打断子进程，
                # 由本脚本统一通过 Job 对象终止；POSIX 下独立成会话
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0),
                start_new_session=not _IS_WINDOWS,
            )
        except FileNotFoundError as exc:
            _log(f"[dev_run] 启动 {name} 失败：{exc}\n")
            _terminate_all(processes, job)
            return 1

        # 立即将子进程纳入 Job，其后续派生的子进程会自动继承 Job 归属
        _assign_to_job(job, proc.pid)

        processes.append((name, proc))
        assert proc.stdout is not None
        thread = threading.Thread(target=_stream_output, args=(name, proc.stdout), daemon=True)
        thread.start()
        threads.append(thread)
        _log(f"[dev_run] 已启动 {name}（pid={proc.pid}）\n")

    _log(f"[dev_run] 前后端已并行启动（前端运行器：{_FRONTEND_RUNNER}），按 Ctrl+C 停止全部服务。\n")

    exit_code = 0
    try:
        # 任一子进程退出即整体退出
        while True:
            for name, proc in processes:
                ret = proc.poll()
                if ret is not None:
                    _log(f"[dev_run] {name} 已退出（code={ret}）\n")
                    exit_code = ret or exit_code
                    raise _ServiceExited
            for _, proc in processes:
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    continue
    except (KeyboardInterrupt, _ServiceExited):
        pass
    finally:
        _terminate_all(processes, job)

    return exit_code


def _terminate_all(processes: list[tuple[str, subprocess.Popen[bytes]]], job: int | None) -> None:
    """终止全部子进程及其派生进程，超时后强制结束。"""
    for name, proc in processes:
        if proc.poll() is not None:
            continue
        _log(f"[dev_run] 正在停止 {name}...\n")
        if not _IS_WINDOWS:
            # POSIX 下子进程已独立成会话，向整个进程组发送 SIGINT
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)

    if _IS_WINDOWS:
        # 关闭 Job 句柄触发内核原子式终止整棵进程树（含自动重载子进程）
        _close_job(job)

    for name, proc in processes:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _log(f"[dev_run] {name} 未在超时内退出，强制结束。\n")
            proc.kill()


class _ServiceExited(Exception):
    """内部信号：某个服务已退出，触发整体关闭。"""


if __name__ == "__main__":
    raise SystemExit(main())
