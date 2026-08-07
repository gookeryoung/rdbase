"""scripts/deploy.py 单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_deploy():
    path = Path(__file__).resolve().parent.parent / "scripts" / "deploy.py"
    spec = importlib.util.spec_from_file_location("_script_deploy", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


deploy = _load_deploy()


def test_load_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nKEY1=value1\nKEY2=\"quoted\"\nKEY3='single'\nBAD\n",
        encoding="utf-8",
    )
    env = deploy.load_env(env_file)
    assert env == {"KEY1": "value1", "KEY2": "quoted", "KEY3": "single"}


def test_load_env_missing(tmp_path: Path) -> None:
    assert deploy.load_env(tmp_path / "nope.env") == {}


def test_find_app_dir(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "requirements.txt").write_text("django\n", encoding="utf-8")
    nested = app / "scripts"
    nested.mkdir()
    found = deploy.find_app_dir(nested)
    assert found.resolve() == app.resolve()


def test_venv_python(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    py = deploy.venv_python(venv)
    if sys.platform == "win32":
        assert py.name == "python.exe"
        assert py.parent.name == "Scripts"
    else:
        assert py.name == "python"
        assert py.parent.name == "bin"


def test_ensure_env_file_copies_template(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    (app / "config" / ".env.example").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
    env_file = deploy.ensure_env_file(app)
    assert env_file.exists()
    assert env_file.read_text(encoding="utf-8") == "DJANGO_SECRET_KEY=x\n"


def test_ensure_env_file_keeps_existing(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    (app / "config" / ".env.example").write_text("NEW=x\n", encoding="utf-8")
    (app / ".env").write_text("EXISTING=y\n", encoding="utf-8")
    deploy.ensure_env_file(app)
    assert (app / ".env").read_text(encoding="utf-8") == "EXISTING=y\n"


def test_build_runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / ".env").write_text("DB_NAME=rdbase\nDJANGO_SECRET_KEY=secret\n", encoding="utf-8")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "rdbase.settings.dev")
    env = deploy.build_runtime_env(app)
    assert env["DB_NAME"] == "rdbase"
    assert env["DJANGO_SETTINGS_MODULE"] == "rdbase.settings.dev"


def test_build_runtime_env_defaults_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    env = deploy.build_runtime_env(app)
    assert env["DJANGO_SETTINGS_MODULE"] == "rdbase.settings.prod"


def test_main_skip_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "config").mkdir(parents=True)
    (app / "config" / ".env.example").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
    (app / "wheels").mkdir()
    (app / "requirements.txt").write_text("django\n", encoding="utf-8")

    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        calls.append((cmd, cwd))

    monkeypatch.setattr(deploy, "run", fake_run)
    code = deploy.main(["--app-dir", str(app), "--skip-install"])
    assert code == 0
    # venv 创建 + migrate + collectstatic 均通过 run
    cmds = [" ".join(c) for c, _ in calls]
    assert any("venv" in c for c in cmds)
    assert any("migrate" in c for c in cmds)
    assert any("collectstatic" in c for c in cmds)


def test_parse_args() -> None:
    args = deploy.parse_args(["--no-venv", "--skip-install"])
    assert args.no_venv is True
    assert args.skip_install is True
