"""离线部署脚本：在内网目标机安装依赖、初始化数据库并生成运行配置。

前置条件：
    - 目标机已预装 Python ≥ 3.10（含 venv 与 pip 模块）。
    - 离线包已解压，目录内含 backend/、wheels/、requirements.txt、config/。

用法：
    python scripts/deploy.py [--app-dir <离线包根>] [--no-venv] [--skip-install]
                             [--create-superuser] [--superuser-username <name>]
                             [--superuser-email <email>]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("deploy")


def setup_logging(verbose: bool) -> None:
    """配置日志输出到 stdout。"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


def load_env(path: Path) -> dict[str, str]:
    """简易 .env 解析器：返回 KEY=VALUE 字典，跳过注释与空行。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def find_app_dir(start: Path) -> Path:
    """从 start 向上查找含 requirements.txt 与 backend/ 的离线包根目录。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "requirements.txt").exists() and (candidate / "backend").is_dir():
            return candidate
    return current


def venv_python(venv_dir: Path) -> Path:
    """返回虚拟环境内的 Python 可执行文件路径。"""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_venv(app_dir: Path, use_venv: bool) -> Path:
    """创建（若不存在）并返回用于运行 Django 的 Python 可执行文件。"""
    if not use_venv:
        logger.info("使用系统 Python：%s", sys.executable)
        return Path(sys.executable)
    venv_dir = app_dir / ".venv"
    py = venv_python(venv_dir)
    if not py.exists():
        logger.info("创建虚拟环境：%s", venv_dir)
        run([sys.executable, "-m", "venv", str(venv_dir)], app_dir)
    return py


def ensure_env_file(app_dir: Path) -> Path:
    """确保 app_dir/.env 存在（不存在则从 config/.env.example 复制）。"""
    env_file = app_dir / ".env"
    if not env_file.exists():
        template = app_dir / "config" / ".env.example"
        if template.exists():
            shutil.copy2(template, env_file)
            logger.warning("已从模板生成 .env，请编辑填入生产密钥与数据库配置：%s", env_file)
        else:
            logger.warning("未找到 .env 与模板 config/.env.example，请手动创建 .env")
    return env_file


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    """运行命令并实时转发输出，失败抛 CalledProcessError。"""
    logger.info("运行：%s（cwd=%s）", " ".join(cmd), cwd)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def install_deps(app_dir: Path, python: Path) -> None:
    """从 wheels 离线安装 requirements.txt 中的依赖。"""
    wheels = app_dir / "wheels"
    req = app_dir / "requirements.txt"
    run(
        [str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "-r", str(req)],
        app_dir,
    )


def run_manage(python: Path, app_dir: Path, args: list[str], env: dict[str, str]) -> None:
    """运行 manage.py 子命令。"""
    run([str(python), "manage.py", *args], app_dir / "backend", env=env)


def build_runtime_env(app_dir: Path) -> dict[str, str]:
    """合并系统环境与 .env，并固定 DJANGO_SETTINGS_MODULE 为生产配置。"""
    env = dict(os.environ)
    env.update(load_env(app_dir / ".env"))
    env.setdefault("DJANGO_SETTINGS_MODULE", "rdbase.settings.prod")
    return env


def create_superuser(python: Path, app_dir: Path, username: str, email: str, password: str) -> None:
    """非交互式创建超级用户（需 DJANGO_SUPERUSER_PASSWORD 或 --superuser-password）。"""
    super_env = {**build_runtime_env(app_dir), "DJANGO_SUPERUSER_PASSWORD": password}
    run_manage(python, app_dir, ["createsuperuser", "--noinput", "--username", username, "--email", email], super_env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="rdbase 离线部署")
    parser.add_argument("--app-dir", type=Path, default=None, help="离线包根目录（默认向上自动探测）")
    parser.add_argument("--no-venv", action="store_true", help="使用系统 Python，不创建虚拟环境")
    parser.add_argument("--skip-install", action="store_true", help="跳过依赖安装，仅执行 migrate/collectstatic")
    parser.add_argument("--create-superuser", action="store_true", help="部署后创建超级用户")
    parser.add_argument("--superuser-username", default="admin", help="超级用户名")
    parser.add_argument("--superuser-email", default="admin@example.com", help="超级用户邮箱")
    parser.add_argument(
        "--superuser-password", default=os.environ.get("DJANGO_SUPERUSER_PASSWORD", ""), help="超级用户密码"
    )
    parser.add_argument("--verbose", action="store_true", help="启用 DEBUG 日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """部署入口。"""
    args = parse_args(argv)
    setup_logging(args.verbose)

    app_dir = (args.app_dir or find_app_dir(Path(__file__).resolve().parent)).resolve()
    logger.info("离线包根目录：%s", app_dir)

    if not (app_dir / "backend").is_dir():
        logger.error("未找到 backend/ 目录，请通过 --app-dir 指定离线包根")
        return 1

    env_file = ensure_env_file(app_dir)

    python = ensure_venv(app_dir, use_venv=not args.no_venv)

    if not args.skip_install:
        logger.info("离线安装依赖…")
        install_deps(app_dir, python)
    else:
        logger.info("已跳过依赖安装")

    runtime_env = build_runtime_env(app_dir)

    logger.info("应用数据库迁移…")
    run_manage(python, app_dir, ["migrate", "--noinput"], runtime_env)

    logger.info("收集静态文件…")
    run_manage(python, app_dir, ["collectstatic", "--noinput"], runtime_env)

    if args.create_superuser:
        if not args.superuser_password:
            logger.error("创建超级用户需要 --superuser-password 或 DJANGO_SUPERUSER_PASSWORD 环境变量")
            return 1
        logger.info("创建超级用户…")
        create_superuser(python, app_dir, args.superuser_username, args.superuser_email, args.superuser_password)

    venv_py = python if args.no_venv else venv_python(app_dir / ".venv")
    gunicorn_bin = str(venv_py.parent / ("gunicorn.exe" if sys.platform == "win32" else "gunicorn"))
    logger.info("部署完成。启动服务：")
    logger.info("  cd %s", app_dir / "backend")
    logger.info("  （加载 .env 后）%s --config ../config/gunicorn.conf.py rdbase.asgi:application", gunicorn_bin)
    logger.info("请编辑 .env 填入生产密钥与数据库配置：%s", env_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
