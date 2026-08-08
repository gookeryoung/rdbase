"""scripts/restore.py 单元测试。"""

from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path

import pytest


def _load_restore():
    path = Path(__file__).resolve().parent.parent / "scripts" / "restore.py"
    spec = importlib.util.spec_from_file_location("_script_restore", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


restore = _load_restore()


def test_load_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('DB_HOST=h\nDB_PASSWORD="p"\n', encoding="utf-8")
    assert restore.load_env(env_file) == {"DB_HOST": "h", "DB_PASSWORD": "p"}


def test_detect_db_engine() -> None:
    assert restore.detect_db_engine({"DB_ENGINE": "postgres"}) == "postgresql"
    assert restore.detect_db_engine({}) == "sqlite"


def test_sqlite_db_path_default(tmp_path: Path) -> None:
    assert restore.sqlite_db_path({}, tmp_path) == tmp_path / "dbs" / "db.sqlite3"


def test_pg_restore_cmd() -> None:
    env = {"DB_HOST": "h", "DB_PORT": "5432", "DB_USER": "u", "DB_NAME": "n"}
    dump = Path("/tmp/db.dump")
    cmd = restore.pg_restore_cmd(env, dump)
    assert cmd[0] == "pg_restore"
    assert "--clean" in cmd
    assert "--if-exists" in cmd
    assert "--dbname" in cmd and "n" in cmd
    assert str(dump) in cmd


def test_read_manifest(tmp_path: Path) -> None:
    m = tmp_path / "manifest.txt"
    m.write_text("engine=postgresql\ntimestamp=20260101-120000\nversion=0.1.0\n", encoding="utf-8")
    data = restore.read_manifest(m)
    assert data["engine"] == "postgresql"
    assert data["version"] == "0.1.0"


def test_extract_archive(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "manifest.txt").write_text("engine=sqlite\n", encoding="utf-8")
    (src / "db.sqlite3").write_text("DB", encoding="utf-8")
    archive = tmp_path / "b.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src / "manifest.txt", arcname="manifest.txt")
        tar.add(src / "db.sqlite3", arcname="db.sqlite3")
    dest = tmp_path / "out"
    restore.extract_archive(archive, dest)
    assert (dest / "manifest.txt").exists()
    assert (dest / "db.sqlite3").read_text(encoding="utf-8") == "DB"


def test_main_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "app"
    (app / "dbs").mkdir(parents=True)
    target = app / "dbs" / "db.sqlite3"
    target.write_text("OLD", encoding="utf-8")
    (app / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")

    # 构造备份归档
    work = tmp_path / "work"
    work.mkdir()
    (work / "manifest.txt").write_text("engine=sqlite\ntimestamp=20260101-120000\n", encoding="utf-8")
    (work / "db.sqlite3").write_text("NEW", encoding="utf-8")
    (work / ".env").write_text("DB_ENGINE=sqlite\nRESTORED=1\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("manifest.txt", "db.sqlite3", ".env"):
            tar.add(work / name, arcname=name)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        calls.append(cmd)

    monkeypatch.setattr(restore, "run", fake_run)
    code = restore.main(["--file", str(archive), "--app-dir", str(app), "--yes"])
    assert code == 0
    assert target.read_text(encoding="utf-8") == "NEW"
    assert (app / ".env.before-restore").exists()
    assert "RESTORED=1" in (app / ".env").read_text(encoding="utf-8")
    # migrate 被调用
    assert any("migrate" in " ".join(c) for c in calls)


def test_main_requires_yes(tmp_path: Path) -> None:
    archive = tmp_path / "b.tar.gz"
    archive.write_text("x", encoding="utf-8")
    code = restore.main(["--file", str(archive), "--app-dir", str(tmp_path)])
    assert code == 1


def test_main_missing_archive(tmp_path: Path) -> None:
    code = restore.main(["--file", str(tmp_path / "nope.tar.gz"), "--yes"])
    assert code == 1
