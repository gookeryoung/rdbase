"""离线打包脚本：在联网环境构建可部署到内网的离线发布包。

产物布局（dist/rdbase-offline-<version>/）：
    backend/          后端代码（含 migrations、manage.py）
    frontend/dist/    前端构建产物
    staticfiles/      collectstatic 产物
    wheels/           离线 Python wheels
    requirements.txt  冻结依赖清单（uv export --frozen --no-dev --no-emit-project）
    config/           .env.example、nginx.conf、gunicorn.conf.py
    scripts/          deploy.py、backup.py、restore.py
    README.md         离线部署说明

用法：
    uv run python scripts/offline_pack.py [--skip-frontend] [--skip-wheels] [--no-archive] [--verbose]

前提：打包机须与目标机 OS/架构/Python 主版本一致，以确保 wheels 二进制兼容。
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

logger = logging.getLogger("offline_pack")

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = ROOT / "dist"

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def setup_logging(verbose: bool) -> None:
    """配置日志输出到 stdout。"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


def read_version(init_file: Path) -> str:
    """从 backend/rdbase/__init__.py 读取 __version__。"""
    text = init_file.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m:
        raise RuntimeError(f"未在 {init_file} 中找到 __version__")
    return m.group(1)


def run(cmd: list[str], cwd: Path) -> None:
    """运行命令并实时转发输出，失败抛 CalledProcessError。"""
    logger.info("运行：%s（cwd=%s）", " ".join(cmd), cwd)
    subprocess.run(cmd, cwd=cwd, check=True)


def build_frontend() -> None:
    """构建前端产物到 frontend/dist。"""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    run([npm, "run", "build"], FRONTEND_DIR)


def collect_static() -> None:
    """收集后端静态文件到 backend/staticfiles。"""
    run([sys.executable, "manage.py", "collectstatic", "--noinput"], BACKEND_DIR)


def export_requirements(out_file: Path) -> None:
    """导出冻结依赖清单（仅运行时依赖，不含项目自身与 dev 依赖）到 out_file。"""
    run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "-o", str(out_file)], ROOT)


def download_wheels(requirements_file: Path, wheels_dir: Path) -> None:
    """下载全部依赖 wheels 到 wheels_dir。

    uv 创建的虚拟环境默认不含 pip，先安装 pip 再用 pip download 拉取 wheels。
    """
    wheels_dir.mkdir(parents=True, exist_ok=True)
    run(["uv", "pip", "install", "pip"], ROOT)
    run(
        [sys.executable, "-m", "pip", "download", "-r", str(requirements_file), "-d", str(wheels_dir)],
        ROOT,
    )


def _ignore_backend_junk(directory: str, names: list[str]) -> list[str]:  # noqa: ARG001
    """shutil.copytree 忽略规则：缓存、运行时产物、测试缓存。"""
    ignored: list[str] = []
    for n in names:
        if n in ("__pycache__", "db", "staticfiles", "media", ".pytest_cache") or n.endswith(".pyc"):
            ignored.append(n)
    return ignored


def write_gunicorn_conf(path: Path) -> None:
    """生成 gunicorn 配置模板。"""
    lines = [
        '"""gunicorn 配置：worker 数与超时可由环境变量覆盖。"""',
        "import os",
        "",
        'bind = "0.0.0.0:8000"',
        'workers = int(os.environ.get("GUNICORN_WORKERS", "4"))',
        'worker_class = "uvicorn.workers.UvicornWorker"',
        'timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))',
        "graceful_timeout = 30",
        "preload_app = True",
        'accesslog = "-"',
        'errorlog = "-"',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assemble_bundle(bundle_dir: Path, root: Path) -> None:
    """组装离线包目录结构（代码、前端产物、静态文件、配置、脚本、README）。"""
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    # 后端代码
    shutil.copytree(root / "backend", bundle_dir / "backend", ignore=_ignore_backend_junk)
    # 前端构建产物
    shutil.copytree(root / "frontend" / "dist", bundle_dir / "frontend" / "dist")
    # 后端静态文件
    src_static = root / "backend" / "staticfiles"
    if src_static.exists():
        shutil.copytree(src_static, bundle_dir / "staticfiles")
    # 配置
    config_dir = bundle_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / ".env.example", config_dir / ".env.example")
    shutil.copy2(root / "docker" / "nginx.conf", config_dir / "nginx.conf")
    write_gunicorn_conf(config_dir / "gunicorn.conf.py")
    # 部署脚本
    scripts_dst = bundle_dir / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    for name in ("deploy.py", "backup.py", "restore.py"):
        src = root / "scripts" / name
        if src.exists():
            shutil.copy2(src, scripts_dst / name)


def write_bundle_readme(bundle_dir: Path, version: str) -> None:
    """生成离线包内的部署说明 README.md。"""
    lines = [
        f"# rdbase 离线部署包（v{version}）",
        "",
        "## 目录结构",
        "",
        "```",
        "backend/          后端代码（含数据库迁移）",
        "frontend/dist/    前端构建产物（nginx 托管）",
        "staticfiles/      后端静态文件",
        "wheels/           离线 Python 依赖",
        "requirements.txt  冻结依赖清单",
        "config/           .env.example、nginx.conf、gunicorn.conf.py",
        "scripts/          deploy.py、backup.py、restore.py",
        "```",
        "",
        "## 前置条件",
        "",
        "- 目标机已安装 Python ≥ 3.10（含 venv 与 pip 模块）。",
        "- 若使用 PostgreSQL，目标机须安装 postgresql-client（提供 pg_dump/pg_restore）。",
        "- 若使用 nginx 托管前端，须单独安装 nginx 并指向 frontend/dist。",
        "",
        "## 部署",
        "",
        "```bash",
        "# 1. 解压离线包到目标目录（如 /opt/rdbase）",
        "tar -xzf rdbase-offline-<version>.tar.gz -C /opt/",
        "cd /opt/rdbase-offline-<version>",
        "",
        "# 2. 一键部署（创建虚拟环境、安装依赖、迁移、收集静态、生成 .env）",
        "python scripts/deploy.py",
        "",
        "# 3. 编辑 .env 填入生产密钥与数据库配置",
        "vi .env",
        "",
        "# 4. 应用迁移并启动",
        "python scripts/deploy.py --skip-install   # 仅执行 migrate/collectstatic",
        ". ./.env && .venv/bin/gunicorn --config config/gunicorn.conf.py rdbase.asgi:application",
        "```",
        "",
        "## 备份",
        "",
        "```bash",
        "python scripts/backup.py --keep 10   # 备份平台库与配置，保留最近 10 份",
        "```",
        "",
        "## 迁移/恢复",
        "",
        "```bash",
        "python scripts/restore.py --file backups/rdbase-backup-<时间戳>.tar.gz --yes",
        "```",
        "",
        "详细参数见各脚本 `--help`。",
    ]
    (bundle_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_archive(bundle_dir: Path, archive_path: Path) -> None:
    """将 bundle 目录打包为 tar.gz。"""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_dir, arcname=bundle_dir.name)
    logger.info("已生成归档：%s", archive_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="构建 rdbase 离线发布包")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建（复用已有 frontend/dist）")
    parser.add_argument("--skip-wheels", action="store_true", help="跳过 wheels 下载（用于调试组装流程）")
    parser.add_argument("--no-archive", action="store_true", help="不生成 tar.gz 归档，仅保留目录")
    parser.add_argument("--verbose", action="store_true", help="启用 DEBUG 日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """打包入口。"""
    args = parse_args(argv)
    setup_logging(args.verbose)

    version = read_version(ROOT / "backend" / "rdbase" / "__init__.py")
    bundle_dir = DIST_DIR / f"rdbase-offline-{version}"
    logger.info("开始构建离线包 v%s → %s", version, bundle_dir)

    if not args.skip_frontend:
        logger.info("构建前端…")
        build_frontend()
    if not (FRONTEND_DIR / "dist").exists():
        logger.error("frontend/dist 不存在，请去掉 --skip-frontend 或先执行 npm run build")
        return 1

    logger.info("收集后端静态文件…")
    collect_static()

    logger.info("组装离线包目录…")
    assemble_bundle(bundle_dir, ROOT)
    write_bundle_readme(bundle_dir, version)

    logger.info("导出依赖清单…")
    export_requirements(bundle_dir / "requirements.txt")

    if not args.skip_wheels:
        logger.info("下载离线 wheels…")
        download_wheels(bundle_dir / "requirements.txt", bundle_dir / "wheels")
    else:
        logger.warning("已跳过 wheels 下载，离线包不完整")

    if not args.no_archive:
        create_archive(bundle_dir, DIST_DIR / f"rdbase-offline-{version}.tar.gz")

    logger.info("离线包构建完成：%s", bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
