"""一键备份脚本：备份平台数据库与运行配置到归档。

支持两种平台数据库：
    - PostgreSQL：调用 pg_dump（custom 格式），恢复用 pg_restore。
    - SQLite：直接复制数据库文件。

数据库类型由环境变量 DB_ENGINE 决定，未设置时按 DB_HOST 是否存在推断
（有 DB_HOST 视为 PostgreSQL，否则视为 SQLite）。

用法：
    python scripts/backup.py [--app-dir <应用根>] [--keep 10] [--backup-dir <dir>]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("backup")

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
    return app_dir / "backend" / "db" / "db.sqlite3"


def pg_dump_cmd(env: dict[str, str], out_file: Path) -> list[str]:
    """构造 pg_dump 命令（custom 格式）。"""
    return [
        "pg_dump",
        "--host",
        env.get("DB_HOST", "localhost"),
        "--port",
        env.get("DB_PORT", "5432"),
        "--username",
        env.get("DB_USER", "rdbase"),
        "--format",
        "custom",
        "--file",
        str(out_file),
        env.get("DB_NAME", "rdbase"),
    ]


def timestamp() -> str:
    """返回 YYYYMMDD-HHMMSS 时间戳。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_filename(ts: str) -> str:
    """构造备份归档文件名。"""
    return f"rdbase-backup-{ts}.tar.gz"


def write_manifest(path: Path, engine: str, ts: str, version: str | None) -> None:
    """写入备份清单（engine/timestamp/version）。"""
    lines = [f"engine={engine}", f"timestamp={ts}"]
    if version:
        lines.append(f"version={version}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def create_archive(members: dict[str, Path], archive_path: Path) -> None:
    """将多个文件打包为 tar.gz（members: {归档内路径: 源文件}）。"""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for arcname, src in members.items():
            tar.add(src, arcname=arcname)
    logger.info("已生成备份归档：%s", archive_path)


def prune_old_backups(backup_dir: Path, keep: int) -> list[Path]:
    """按修改时间保留最近 keep 份备份，删除多余项，返回已删除列表。"""
    archives = sorted(
        backup_dir.glob("rdbase-backup-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted: list[Path] = []
    for path in archives[keep:]:
        path.unlink()
        deleted.append(path)
        logger.info("已清理旧备份：%s", path)
    return deleted


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    """运行命令并实时转发输出，失败抛 CalledProcessError。"""
    logger.info("运行：%s（cwd=%s）", " ".join(cmd), cwd)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def backup_postgresql(env: dict[str, str], out_file: Path) -> None:
    """调用 pg_dump 备份 PostgreSQL 数据库。"""
    cmd_env = dict(env)
    if env.get("DB_PASSWORD"):
        cmd_env["PGPASSWORD"] = env["DB_PASSWORD"]
    run(pg_dump_cmd(env, out_file), Path.cwd(), env=cmd_env)


def backup_sqlite(db_file: Path, out_file: Path) -> None:
    """复制 SQLite 数据库文件到备份目录。"""
    if not db_file.exists():
        raise FileNotFoundError(f"SQLite 数据库文件不存在：{db_file}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_file, out_file)
    logger.info("已复制 SQLite 数据库：%s → %s", db_file, out_file)


def read_version(app_dir: Path) -> str | None:
    """读取后端 __version__（若可用）。"""
    init_file = app_dir / "backend" / "rdbase" / "__init__.py"
    if not init_file.exists():
        return None
    text = init_file.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="rdbase 一键备份")
    parser.add_argument("--app-dir", type=Path, default=None, help="应用根目录（默认脚本上级目录）")
    parser.add_argument("--keep", type=int, default=10, help="保留最近 N 份备份（默认 10）")
    parser.add_argument("--backup-dir", type=Path, default=None, help="备份输出目录（默认 <app>/backups）")
    parser.add_argument("--verbose", action="store_true", help="启用 DEBUG 日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """备份入口。"""
    args = parse_args(argv)
    setup_logging(args.verbose)

    app_dir = (args.app_dir or Path(__file__).resolve().parent.parent).resolve()
    backup_dir = args.backup_dir or (app_dir / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    logger.info("应用根目录：%s", app_dir)

    env = merged_env(app_dir)
    engine = detect_db_engine(env)
    ts = timestamp()
    work = backup_dir / f".tmp-{ts}"
    work.mkdir(parents=True, exist_ok=True)

    try:
        if engine == "postgresql":
            logger.info("备份 PostgreSQL 数据库…")
            backup_postgresql(env, work / _DB_DUMP_PG)
            db_member = _DB_DUMP_PG
        else:
            logger.info("备份 SQLite 数据库…")
            backup_sqlite(sqlite_db_path(env, app_dir), work / _DB_DUMP_SQLITE)
            db_member = _DB_DUMP_SQLITE

        env_file = app_dir / ".env"
        if env_file.exists():
            shutil.copy2(env_file, work / ".env")

        write_manifest(work / _MANIFEST, engine, ts, read_version(app_dir))

        members: dict[str, Path] = {
            _MANIFEST: work / _MANIFEST,
            db_member: work / db_member,
        }
        if (work / ".env").exists():
            members[".env"] = work / ".env"

        archive_path = backup_dir / backup_filename(ts)
        create_archive(members, archive_path)
        prune_old_backups(backup_dir, args.keep)
        logger.info("备份完成：%s", archive_path)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
