"""fspack 打包模式测试：pack_urls SPA 路由 + pack_main 入口编排 + pack 配置.

覆盖：
- pack_urls._spa_fallback：根路径返回 index.html、assets 文件直服务、
  未知路由回退 SPA、前端缺失 404
- pack_urls urlpatterns：/admin/ /health/ /api/v1/ 由 base_urlpatterns 处理
- pack_main.main：设置环境变量、调用 django.setup/migrate/uvicorn.run
- pack 配置：SQLite 便携、DEBUG=False、ROOT_URLCONF=pack_urls
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpResponseBase, StreamingHttpResponse
from django.test import Client, override_settings


def _read_content(response: HttpResponseBase) -> bytes:
    """读取响应内容（兼容 streaming 与非 streaming）."""
    if isinstance(response, StreamingHttpResponse):
        return b"".join(response.streaming_content)
    return response.content


@pytest.fixture
def spa_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """构造临时前端构建产物目录并 patch pack_urls 模块级变量.

    目录结构：
        spa/
          index.html          SPA 入口
          favicon.ico         根级文件
          assets/
            index-abc123.js   JS chunk
            style-def456.css  CSS chunk
    """
    spa_dir = tmp_path / "spa"
    spa_dir.mkdir()
    (spa_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA Root</body></html>", encoding="utf-8")
    (spa_dir / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    assets_dir = spa_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "index-abc123.js").write_text('console.log("app");', encoding="utf-8")
    (assets_dir / "style-def456.css").write_text("body{color:red}", encoding="utf-8")

    import rdbase.pack_urls

    monkeypatch.setattr(rdbase.pack_urls, "_frontend_dist", spa_dir)
    monkeypatch.setattr(rdbase.pack_urls, "_index_html", spa_dir / "index.html")
    return spa_dir


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_spa_root_returns_index_html(spa_dist: Path) -> None:
    """GET / 应返回前端 index.html."""
    client = Client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"SPA Root" in _read_content(response)


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_spa_asset_file_served(spa_dist: Path) -> None:
    """GET /assets/index-abc123.js 应返回 JS 文件内容."""
    client = Client()
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert b"console.log" in _read_content(response)


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_spa_favicon_served(spa_dist: Path) -> None:
    """GET /favicon.ico 应返回根级文件."""
    client = Client()
    response = client.get("/favicon.ico")
    assert response.status_code == 200


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_spa_unknown_route_returns_index_html(spa_dist: Path) -> None:
    """GET /some/unknown/route 应回退到 index.html（SPA 客户端路由）."""
    client = Client()
    response = client.get("/some/unknown/route")
    assert response.status_code == 200
    assert b"SPA Root" in _read_content(response)


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_admin_route_accessible(spa_dist: Path) -> None:
    """GET /admin/ 应由 base_urlpatterns 处理（重定向到登录页）."""
    client = Client()
    response = client.get("/admin/")
    assert response.status_code in (301, 302)


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_health_route_accessible(spa_dist: Path) -> None:
    """GET /health/live 应由 base_urlpatterns 处理，返回 200."""
    client = Client()
    response = client.get("/health/live")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == "ok"


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_api_route_accessible(spa_dist: Path) -> None:
    """GET /api/v1/openapi.json 应由 base_urlpatterns 处理，返回 200."""
    client = Client()
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    schema = json.loads(response.content)
    assert schema["info"]["title"] == "rdbase API"


@override_settings(ROOT_URLCONF="rdbase.pack_urls")
def test_spa_404_when_no_frontend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前端构建产物缺失时，SPA 路由应返回 404."""
    import rdbase.pack_urls

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(rdbase.pack_urls, "_frontend_dist", empty_dir)
    monkeypatch.setattr(rdbase.pack_urls, "_index_html", empty_dir / "index.html")

    client = Client()
    response = client.get("/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# pack_main.main 编排测试
# ---------------------------------------------------------------------------


def test_pack_main_sets_env_and_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() 应设置环境变量、调用 django.setup/migrate/uvicorn.run."""
    # 构造模拟的 dist/src/backend/ 结构
    fake_src = tmp_path / "src"
    fake_backend = fake_src / "backend"
    fake_backend.mkdir(parents=True)
    fake_entry = fake_backend / "pack_main.py"
    fake_entry.write_text("")

    import pack_main

    monkeypatch.setattr(pack_main, "__file__", str(fake_entry))

    # 清除 pytest 预设的 DJANGO_SETTINGS_MODULE，让 setdefault 生效
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    original_path = sys.path[:]

    with (
        patch("django.setup") as mock_setup,
        patch("django.core.management.call_command") as mock_call,
        patch("uvicorn.run") as mock_run,
    ):
        try:
            pack_main.main()
            # 在 finally 恢复前验证环境变量
            assert os.environ["DJANGO_SETTINGS_MODULE"] == "rdbase.settings.pack"
            assert os.environ["RDBASE_DATA_DIR"] == str(tmp_path / "data")
        finally:
            sys.path[:] = original_path

    # 验证 data/ 目录已创建
    expected_data = tmp_path / "data"
    assert expected_data.is_dir()

    # 验证调用
    mock_setup.assert_called_once()
    mock_call.assert_called_once_with("migrate", interactive=False)
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == "rdbase.asgi:application"
    assert mock_run.call_args.kwargs["host"] == "127.0.0.1"
    assert mock_run.call_args.kwargs["port"] == 8000


# ---------------------------------------------------------------------------
# pack 配置测试
# ---------------------------------------------------------------------------


def test_pack_settings_config() -> None:
    """pack 配置应使用 SQLite、关闭 DEBUG、指向 pack_urls."""
    os.environ["RDBASE_DATA_DIR"] = "/tmp/rdbase-pack-test"
    try:
        sys.modules.pop("rdbase.settings.pack", None)
        from rdbase.settings import pack

        assert pack.DEBUG is False
        assert pack.ALLOWED_HOSTS == ["*"]
        assert pack.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
        assert pack.REDIS_URL == ""
        assert pack.ROOT_URLCONF == "rdbase.pack_urls"
        assert pack.CORS_ALLOW_ALL_ORIGINS is True
        assert pack.FRONTEND_DIST.name == "spa"
        assert pack.FRONTEND_DIST.parent.name == "staticfiles"
    finally:
        os.environ.pop("RDBASE_DATA_DIR", None)
        sys.modules.pop("rdbase.settings.pack", None)
