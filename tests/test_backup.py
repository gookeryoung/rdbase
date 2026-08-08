"""scripts/backup.py 单元测试。"""

from __future__ import annotations

import importlib.util
import os
import tarfile
import time
from pathlib import Path

import pytest


def _load_backup():
    path = Path(__file__).resolve().parent.parent / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("_script_backup", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


backup = _load_backup()


def test_load_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('# c\nDB_HOST=h\nDB_PASSWORD="p"\n', encoding="utf-8")
    assert backup.load_env(env_file) == {"DB_HOST": "h", "DB_PASSWORD": "p"}


def test_detect_db_engine() -> None:
    assert backup.detect_db_engine({"DB_ENGINE": "postgresql"}) == "postgresql"
    assert backup.detect_db_engine({"DB_ENGINE": "PG"}) == "postgresql"
    assert backup.detect_db_engine({"DB_ENGINE": "sqlite3"}) == "sqlite"
    assert backup.detect_db_engine({"DB_HOST": "db.local"}) == "postgresql"
    assert backup.detect_db_engine({}) == "sqlite"


def test_sqlite_db_path_explicit(tmp_path: Path) -> None:
    p = backup.sqlite_db_path({"SQLITE_PATH": "/data/x.sqlite3"}, tmp_path)
    assert p == Path("/data/x.sqlite3")


def test_sqlite_db_path_from_db_name_relative(tmp_path: Path) -> None:
    p = backup.sqlite_db_path({"DB_NAME": "app.sqlite3"}, tmp_path)
    assert p == tmp_path / "app.sqlite3"


def test_sqlite_db_path_from_db_name_absolute() -> None:
    p = backup.sqlite_db_path({"DB_NAME": "/var/db/x.db"}, Path("/tmp"))
    assert p == Path("/var/db/x.db")


def test_sqlite_db_path_default(tmp_path: Path) -> None:
    p = backup.sqlite_db_path({}, tmp_path)
    assert p == tmp_path / "dbs" / "db.sqlite3"


def test_pg_dump_cmd() -> None:
    env = {"DB_HOST": "h", "DB_PORT": "5433", "DB_USER": "u", "DB_NAME": "n"}
    out = Path("/tmp/x.dump")
    cmd = backup.pg_dump_cmd(env, out)
    assert cmd[0] == "pg_dump"
    assert "--host" in cmd and "h" in cmd
    assert "--port" in cmd and "5433" in cmd
    assert "--username" in cmd and "u" in cmd
    assert "--format" in cmd and "custom" in cmd
    assert "--file" in cmd and str(out) in cmd
    assert cmd[-1] == "n"


def test_backup_filename() -> None:
    assert backup.backup_filename("20260101-120000") == "rdbase-backup-20260101-120000.tar.gz"


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = tmp_path / "manifest.txt"
    backup.write_manifest(m, "postgresql", "20260101-120000", "0.1.0")
    data = backup.read_manifest(m)
    assert data == {"engine": "postgresql", "timestamp": "20260101-120000", "version": "0.1.0"}


def test_create_archive(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("y", encoding="utf-8")
    archive = tmp_path / "out.tar.gz"
    backup.create_archive({"a.txt": a, "sub/b.txt": b}, archive)
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "a.txt" in names
    assert "sub/b.txt" in names


def test_prune_old_backups(tmp_path: Path) -> None:
    files = []
    for i in range(5):
        f = tmp_path / f"rdbase-backup-00{i}.tar.gz"
        f.write_text("x", encoding="utf-8")
        # 越小越旧
        ts = time.time() - (5 - i) * 100
        os.utime(f, (ts, ts))
        files.append(f)
    deleted = backup.prune_old_backups(tmp_path, keep=2)
    assert len(deleted) == 3
    remaining = sorted(tmp_path.glob("rdbase-backup-*.tar.gz"))
    assert len(remaining) == 2


def test_read_version(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "backend" / "rdbase").mkdir(parents=True)
    (app / "backend" / "rdbase" / "__init__.py").write_text('__version__ = "3.4.5"\n', encoding="utf-8")
    assert backup.read_version(app) == "3.4.5"


def test_read_version_missing(tmp_path: Path) -> None:
    assert backup.read_version(tmp_path) is None


def test_main_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "app"
    (app / "dbs").mkdir(parents=True)
    db = app / "dbs" / "db.sqlite3"
    db.write_text("SQLITE_CONTENT", encoding="utf-8")
    (app / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")
    (app / "backend" / "rdbase").mkdir(parents=True)
    (app / "backend" / "rdbase" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")

    code = backup.main(["--app-dir", str(app), "--keep", "5"])
    assert code == 0
    archives = list((app / "backups").glob("rdbase-backup-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as tar:
        names = tar.getnames()
        mf = tar.extractfile("manifest.txt")
        assert mf is not None
        manifest_tmp = tmp_path / "_manifest.txt"
        manifest_tmp.write_bytes(mf.read())
    assert "db.sqlite3" in names
    assert "manifest.txt" in names
    manifest = backup.read_manifest(manifest_tmp)
    assert manifest["engine"] == "sqlite"
    assert manifest["version"] == "0.1.0"
