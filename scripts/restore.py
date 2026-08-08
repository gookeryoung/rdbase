"""迁移/恢复脚本：从备份归档恢复平台数据库与配置。

支持 PostgreSQL（pg_restore --clean --if-exists）与 SQLite（文件替换）。
恢复前会备份当前 .env 到 .env.before-restore，恢复后执行 migrate 对齐 schema。

用法：
    python scripts/restore.py --file <备份归档> [--app-dir <应用根>] [--yes]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

logger = logging.getLogger("restore")

_MANIFEST = "manifest.txt"
_DB_DUMP_PG = "db.dump"
_DB_DUMP_SQLITE = "db.sqlite3"


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


def merged_env(app_dir: Path) -> dict[str, str]:
    """合并系统环境与 .env。"""
    env = dict(os.environ)
    env.update(load_env(app_dir / ".env"))
    return env


def detect_db_engine(env: dict[str, str]) -> str:
    """检测平台数据库引擎，返回 'postgresql' 或 'sqlite'。"""
    engine = env.get("DB_ENGINE", "").strip().lower()
    if engine in ("postgresql", "postgres", "pg"):
        return "postgresql"
    if engine in ("sqlite", "sqlite3"):
        return "sqlite"
    return "postgresql" if env.get("DB_HOST") else "sqlite"


def sqlite_db_path(env: dict[str, str], app_dir: Path) -> Path:
    """解析 SQLite 数据库文件路径。"""
    explicit = env.get("SQLITE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    name = env.get("DB_NAME", "").strip()
    if name and (name.endswith(".sqlite3") or name.endswith(".sqlite") or name.endswith(".db")):
        path = Path(name)
        return path if path.is_absolute() else app_dir / path
    return app_dir / "dbs" / "db.sqlite3"


def pg_restore_cmd(env: dict[str, str], dump_file: Path) -> list[str]:
    """构造 pg_restore 命令（清理后重建）。"""
    return [
        "pg_restore",
        "--host",
        env.get("DB_HOST", "localhost"),
        "--port",
        env.get("DB_PORT", "5432"),
        "--username",
        env.get("DB_USER", "rdbase"),
        "--clean",
        "--if-exists",
        "--dbname",
        env.get("DB_NAME", "rdbase"),
        str(dump_file),
    ]


def read_manifest(path: Path) -> dict[str, str]:
    """读取备份清单。"""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """解压 tar.gz 到 dest_dir（兼容 Python 3.10/3.11 无 filter 参数）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            tar.extractall(dest_dir, filter="data")  # type: ignore[call-arg]
        except TypeError:
            tar.extractall(dest_dir)


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    """运行命令并实时转发输出，失败抛 CalledProcessError。"""
    logger.info("运行：%s（cwd=%s）", " ".join(cmd), cwd)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def restore_postgresql(env: dict[str, str], dump_file: Path) -> None:
    """调用 pg_restore 恢复 PostgreSQL 数据库。"""
    cmd_env = dict(env)
    if env.get("DB_PASSWORD"):
        cmd_env["PGPASSWORD"] = env["DB_PASSWORD"]
    run(pg_restore_cmd(env, dump_file), Path.cwd(), env=cmd_env)


def restore_sqlite(dump_file: Path, target: Path) -> None:
    """用备份文件替换目标 SQLite 数据库文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dump_file, target)
    logger.info("已恢复 SQLite 数据库：%s → %s", dump_file, target)


def venv_python(venv_dir: Path) -> Path:
    """返回虚拟环境内的 Python 可执行文件路径。"""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def run_migrate(app_dir: Path, env: dict[str, str]) -> None:
    """恢复后执行 migrate 对齐 schema（使用 .venv 或系统 Python）。"""
    venv_py = venv_python(app_dir / ".venv")
    python = venv_py if venv_py.exists() else Path(sys.executable)
    migrate_env = {**env, "DJANGO_SETTINGS_MODULE": env.get("DJANGO_SETTINGS_MODULE", "rdbase.settings.prod")}
    run([str(python), "manage.py", "migrate", "--noinput"], app_dir / "backend", env=migrate_env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="rdbase 迁移/恢复")
    parser.add_argument("--file", type=Path, required=True, help="备份归档路径")
    parser.add_argument("--app-dir", type=Path, default=None, help="应用根目录（默认脚本上级目录）")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接恢复")
    parser.add_argument("--verbose", action="store_true", help="启用 DEBUG 日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """恢复入口。"""
    args = parse_args(argv)
    setup_logging(args.verbose)

    app_dir = (args.app_dir or Path(__file__).resolve().parent.parent).resolve()
    archive = args.file.resolve()
    if not archive.exists():
        logger.error("备份归档不存在：%s", archive)
        return 1
    logger.info("应用根目录：%s", app_dir)
    logger.info("备份归档：%s", archive)

    if not args.yes:
        logger.warning("恢复将覆盖当前数据库与 .env，确认请加 --yes")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        extract_archive(archive, Path(tmp))
        manifest_path = Path(tmp) / _MANIFEST
        if not manifest_path.exists():
            logger.error("归档缺少 manifest.txt，无法识别备份类型")
            return 1
        manifest = read_manifest(manifest_path)
        engine = manifest.get("engine", detect_db_engine(merged_env(app_dir)))

        env = merged_env(app_dir)

        if engine == "postgresql":
            dump_file = Path(tmp) / _DB_DUMP_PG
            if not dump_file.exists():
                logger.error("归档缺少 %s", _DB_DUMP_PG)
                return 1
            logger.info("恢复 PostgreSQL 数据库…")
            restore_postgresql(env, dump_file)
        else:
            dump_file = Path(tmp) / _DB_DUMP_SQLITE
            if not dump_file.exists():
                logger.error("归档缺少 %s", _DB_DUMP_SQLITE)
                return 1
            logger.warning("SQLite 恢复将覆盖文件，请确保服务已停止")
            restore_sqlite(dump_file, sqlite_db_path(env, app_dir))

        # 恢复 .env（先备份当前）
        restored_env = Path(tmp) / ".env"
        current_env = app_dir / ".env"
        if restored_env.exists():
            if current_env.exists():
                shutil.copy2(current_env, app_dir / ".env.before-restore")
                logger.info("已备份当前 .env 到 .env.before-restore")
            shutil.copy2(restored_env, current_env)
            logger.info("已恢复 .env")

        logger.info("执行 migrate 对齐 schema…")
        run_migrate(app_dir, merged_env(app_dir))
        logger.info("恢复完成")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
