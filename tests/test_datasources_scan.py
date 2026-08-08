"""datasources 扫描服务与接口测试."""

from __future__ import annotations

import json
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.models import DataSource, EngineType
from apps.datasources.scanner import scan_sqlite_files
from django.core.management import call_command
from django.http import HttpResponse
from django.test import Client


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _post(
    client: Client,
    url: str,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 POST 请求（无请求体）."""
    h = headers or {}
    return cast(HttpResponse, client.post(url, **h))


# ---------- scanner 服务 ----------


@pytest.mark.django_db
def test_scan_empty_directory(tmp_path: Path) -> None:
    """空目录扫描应返回零结果."""
    result = scan_sqlite_files(tmp_path)
    assert result.directory == tmp_path
    assert result.scanned == 0
    assert result.created == []
    assert result.skipped == []


@pytest.mark.django_db
def test_scan_creates_new_files(tmp_path: Path) -> None:
    """新 SQLite 文件应被注册为数据源，写入绝对路径与默认分组/标签."""
    (tmp_path / "foo.db").touch()
    (tmp_path / "bar.sqlite").touch()
    (tmp_path / "baz.sqlite3").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 3
    assert len(result.created) == 3
    names = {ds.name for ds in result.created}
    assert names == {"foo", "bar", "baz"}
    for ds in result.created:
        assert ds.engine == EngineType.SQLITE
        assert ds.group == "auto-scan"
        assert ds.tags == ["auto-scanned"]
        assert Path(ds.database).exists()


@pytest.mark.django_db
def test_scan_skips_registered(tmp_path: Path) -> None:
    """已注册的文件应被跳过."""
    db_file = tmp_path / "exists.db"
    db_file.touch()
    DataSource.objects.create(
        name="exists",
        engine=EngineType.SQLITE,
        database=str(db_file.resolve()),
    )
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 1
    assert result.created == []
    assert len(result.skipped) == 1
    assert result.skipped[0] == db_file.resolve().as_posix()


@pytest.mark.django_db
def test_scan_name_conflict_appends_suffix(tmp_path: Path) -> None:
    """名称冲突时应追加 -2 后缀."""
    DataSource.objects.create(name="conflict", engine=EngineType.SQLITE, database=":memory:")
    (tmp_path / "conflict.db").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 1
    assert len(result.created) == 1
    assert result.created[0].name == "conflict-2"


@pytest.mark.django_db
def test_scan_name_conflict_multiple(tmp_path: Path) -> None:
    """多次名称冲突应连续追加后缀."""
    DataSource.objects.create(name="dup", engine=EngineType.SQLITE, database=":memory:")
    DataSource.objects.create(name="dup-2", engine=EngineType.SQLITE, database=":memory:")
    (tmp_path / "dup.db").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.created[0].name == "dup-3"


@pytest.mark.django_db
def test_scan_custom_directory(tmp_path: Path) -> None:
    """自定义目录应被使用并体现在结果中."""
    (tmp_path / "custom.db").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.directory == tmp_path
    assert len(result.created) == 1


@pytest.mark.django_db
def test_scan_nonexistent_directory() -> None:
    """不存在的目录应返回空结果，不抛异常."""
    result = scan_sqlite_files(Path("/nonexistent/path/xyz_rdbase"))
    assert result.scanned == 0
    assert result.created == []
    assert result.skipped == []


@pytest.mark.django_db
def test_scan_not_recursive(tmp_path: Path) -> None:
    """子目录内的文件不应被扫描."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "top.db").touch()
    (sub / "nested.db").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 1
    assert len(result.created) == 1
    assert result.created[0].name == "top"


@pytest.mark.django_db
def test_scan_ignores_non_sqlite_files(tmp_path: Path) -> None:
    """非 SQLite 文件应被忽略."""
    (tmp_path / "readme.txt").touch()
    (tmp_path / "data.json").touch()
    (tmp_path / "real.db").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 1
    assert len(result.created) == 1


@pytest.mark.django_db
def test_scan_case_insensitive_suffix(tmp_path: Path) -> None:
    """大写后缀文件应被识别."""
    (tmp_path / "UP.DB").touch()
    result = scan_sqlite_files(tmp_path)
    assert result.scanned == 1
    assert result.created[0].name == "UP"


@pytest.mark.django_db
def test_scan_skips_memory_database_in_dedup(tmp_path: Path) -> None:
    """:memory: 数据源不应干扰去重逻辑."""
    DataSource.objects.create(name="mem", engine=EngineType.SQLITE, database=":memory:")
    (tmp_path / "mem.db").touch()
    result = scan_sqlite_files(tmp_path)
    # mem.db 未被注册（:memory: 不参与去重），应被新建
    assert len(result.created) == 1
    assert result.created[0].name == "mem-2"


# ---------- API 端点 ----------


@pytest.mark.django_db
def test_scan_api_by_admin_succeeds(make_user: Callable[..., User], tmp_path: Path) -> None:
    """管理员调用扫描接口应成功并返回结构化结果."""
    (tmp_path / "api.db").touch()
    admin = make_user(role=Role.ADMIN)
    client = Client()
    url = f"/api/v1/datasources/scan?{urlencode({'directory': str(tmp_path)})}"
    response = _post(client, url, _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["scanned"] == 1
    assert len(body["created"]) == 1
    assert body["created"][0]["name"] == "api"
    assert body["created"][0]["group"] == "auto-scan"
    assert body["skipped"] == []


@pytest.mark.django_db
def test_scan_api_by_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 调用扫描接口应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    url = f"/api/v1/datasources/scan?{urlencode({'directory': str(tmp_path)})}"
    response = _post(client, url, _auth(viewer))
    assert response.status_code == 403


@pytest.mark.django_db
def test_scan_api_without_token_returns_401(tmp_path: Path) -> None:
    """未认证调用扫描接口应返回 401."""
    client = Client()
    url = f"/api/v1/datasources/scan?{urlencode({'directory': str(tmp_path)})}"
    response = _post(client, url)
    assert response.status_code == 401


@pytest.mark.django_db
def test_scan_api_skips_registered(make_user: Callable[..., User], tmp_path: Path) -> None:
    """接口扫描已注册文件应返回 skipped 列表."""
    db_file = tmp_path / "reg.db"
    db_file.touch()
    DataSource.objects.create(
        name="reg",
        engine=EngineType.SQLITE,
        database=str(db_file.resolve()),
    )
    admin = make_user(role=Role.ADMIN)
    client = Client()
    url = f"/api/v1/datasources/scan?{urlencode({'directory': str(tmp_path)})}"
    response = _post(client, url, _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["scanned"] == 1
    assert body["created"] == []
    assert len(body["skipped"]) == 1


# ---------- 管理命令 ----------


@pytest.mark.django_db
def test_scan_management_command(tmp_path: Path) -> None:
    """管理命令应执行扫描并输出摘要."""
    (tmp_path / "cmd.db").touch()
    out = StringIO()
    call_command("scan_datasources", directory=str(tmp_path), stdout=out)
    output = out.getvalue()
    assert "cmd" in output
    assert "新增数据源" in output


@pytest.mark.django_db
def test_scan_management_command_empty(tmp_path: Path) -> None:
    """管理命令扫描空目录应输出无新增."""
    out = StringIO()
    call_command("scan_datasources", directory=str(tmp_path), stdout=out)
    output = out.getvalue()
    assert "无新增或跳过" in output


@pytest.mark.django_db
def test_scan_management_command_skips_registered(tmp_path: Path) -> None:
    """管理命令扫描已注册文件应输出跳过摘要."""
    db_file = tmp_path / "reg.db"
    db_file.touch()
    DataSource.objects.create(
        name="reg",
        engine=EngineType.SQLITE,
        database=str(db_file.resolve()),
    )
    out = StringIO()
    call_command("scan_datasources", directory=str(tmp_path), stdout=out)
    output = out.getvalue()
    assert "跳过已注册" in output
