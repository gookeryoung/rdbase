"""scripts/offline_pack.py 单元测试。"""

from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path

import pytest


def _load_pack():
    path = Path(__file__).resolve().parent.parent / "scripts" / "offline_pack.py"
    spec = importlib.util.spec_from_file_location("_script_offline_pack", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pack = _load_pack()


def test_read_version(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""pkg."""\n__version__ = "1.2.3"\n', encoding="utf-8")
    assert pack.read_version(init) == "1.2.3"


def test_read_version_missing(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""pkg."""\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="__version__"):
        pack.read_version(init)


def test_ignore_backend_junk() -> None:
    names = ["apps", "__pycache__", "db", "staticfiles", "media", "x.pyc", "migrations", ".pytest_cache"]
    ignored = pack._ignore_backend_junk("dummy", names)
    assert set(ignored) == {"__pycache__", "db", "staticfiles", "media", "x.pyc", ".pytest_cache"}


def test_write_gunicorn_conf(tmp_path: Path) -> None:
    out = tmp_path / "gunicorn.conf.py"
    pack.write_gunicorn_conf(out)
    text = out.read_text(encoding="utf-8")
    assert 'bind = "0.0.0.0:8000"' in text
    assert "GUNICORN_WORKERS" in text
    assert "UvicornWorker" in text


def test_assemble_bundle(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "backend" / "apps").mkdir(parents=True)
    (root / "backend" / "apps" / "__init__.py").write_text("", encoding="utf-8")
    (root / "backend" / "rdbase").mkdir(parents=True)
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "frontend" / "dist" / "index.html").write_text("html", encoding="utf-8")
    (root / "backend" / "staticfiles").mkdir(parents=True)
    (root / "backend" / "staticfiles" / "app.css").write_text("css", encoding="utf-8")
    (root / ".env.example").write_text("K=V\n", encoding="utf-8")
    (root / "docker").mkdir()
    (root / "docker" / "nginx.conf").write_text("server{}\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("deploy.py", "backup.py", "restore.py"):
        (scripts / name).write_text("# stub\n", encoding="utf-8")

    bundle = tmp_path / "bundle"
    pack.assemble_bundle(bundle, root)

    assert (bundle / "backend" / "apps" / "__init__.py").exists()
    assert (bundle / "frontend" / "dist" / "index.html").exists()
    assert (bundle / "staticfiles" / "app.css").exists()
    assert (bundle / "config" / ".env.example").exists()
    assert (bundle / "config" / "nginx.conf").exists()
    assert (bundle / "config" / "gunicorn.conf.py").exists()
    for name in ("deploy.py", "backup.py", "restore.py"):
        assert (bundle / "scripts" / name).exists()


def test_write_bundle_readme(tmp_path: Path) -> None:
    pack.write_bundle_readme(tmp_path, "9.9.9")
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "v9.9.9" in text
    assert "deploy.py" in text
    assert "backup.py" in text


def test_create_archive(tmp_path: Path) -> None:
    src = tmp_path / "bundle"
    src.mkdir()
    (src / "file.txt").write_text("hello", encoding="utf-8")
    archive = tmp_path / "out.tar.gz"
    pack.create_archive(src, archive)
    assert archive.exists()
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("file.txt") for n in names)


def test_parse_args() -> None:
    args = pack.parse_args(["--skip-frontend", "--no-archive"])
    assert args.skip_frontend is True
    assert args.no_archive is True
    assert args.skip_wheels is False
