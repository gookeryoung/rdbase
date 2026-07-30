"""datasources 接口测试."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.engine import dispose_all
from apps.datasources.models import DataSource, EngineType
from django.http import HttpResponse
from django.test import Client


def _post(
    client: Client,
    url: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 POST 请求."""
    h = headers or {}
    if body is None:
        return cast(HttpResponse, client.post(url, **h))
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _get(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """发送 GET 请求."""
    h = headers or {}
    return cast(HttpResponse, client.get(url, **h))


def _patch(
    client: Client,
    url: str,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 PATCH 请求."""
    h = headers or {}
    return cast(
        HttpResponse,
        client.patch(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _delete(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """发送 DELETE 请求."""
    h = headers or {}
    return cast(HttpResponse, client.delete(url, **h))


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Iterator[None]:
    """每个测试后清空引擎缓存，避免缓存污染."""

    yield
    dispose_all()


# ---------- 列表 ----------


@pytest.mark.django_db
def test_list_returns_all_datasources(make_user: Callable[..., User]) -> None:
    """列表应返回全部数据源（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    DataSource.objects.create(name="a", engine=EngineType.SQLITE, database=":memory:")
    DataSource.objects.create(name="b", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _get(client, "/api/v1/datasources", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert len(body) == 2
    assert {item["name"] for item in body} == {"a", "b"}


@pytest.mark.django_db
def test_list_without_token_returns_401() -> None:
    """未认证访问列表应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/datasources")
    assert response.status_code == 401


# ---------- 创建 ----------


@pytest.mark.django_db
def test_create_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """管理员创建数据源应成功，密码加密入库."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {
            "name": "mysql-prod",
            "engine": "mysql",
            "host": "10.0.0.1",
            "port": 3306,
            "database": "app",
            "username": "root",
            "password": "s3cret",
            "tags": ["primary"],
        },
        _auth(admin),
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["name"] == "mysql-prod"
    assert "password" not in body
    ds = DataSource.objects.get(name="mysql-prod")
    assert ds.password_encrypted != "s3cret"
    assert ds.get_password() == "s3cret"
    assert ds.created_by == admin


@pytest.mark.django_db
def test_create_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 创建数据源应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "x", "engine": "sqlite", "database": ":memory:"},
        _auth(viewer),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_create_duplicate_name_returns_400(make_user: Callable[..., User]) -> None:
    """重名创建应返回 400."""
    admin = make_user(role=Role.ADMIN)
    DataSource.objects.create(name="dup", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "dup", "engine": "sqlite", "database": ":memory:"},
        _auth(admin),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_invalid_engine_returns_400(make_user: Callable[..., User]) -> None:
    """无效引擎类型应返回 400."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "x", "engine": "oracle", "database": "x"},
        _auth(admin),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_without_password_succeeds(make_user: Callable[..., User]) -> None:
    """无密码创建 SQLite 数据源应成功（不调用 set_password）."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "no-pwd", "engine": "sqlite", "database": ":memory:"},
        _auth(admin),
    )
    assert response.status_code == 201
    ds = DataSource.objects.get(name="no-pwd")
    assert ds.password_encrypted == ""


# ---------- 详情 ----------


@pytest.mark.django_db
def test_retrieve_returns_datasource(make_user: Callable[..., User]) -> None:
    """详情应返回数据源信息（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    ds = DataSource.objects.create(name="x", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _get(client, f"/api/v1/datasources/{ds.pk}", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "x"
    assert "password" not in body


@pytest.mark.django_db
def test_retrieve_unknown_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    response = _get(client, "/api/v1/datasources/99999", _auth(user))
    assert response.status_code == 404


# ---------- 更新 ----------


@pytest.mark.django_db
def test_update_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """管理员更新数据源应成功并刷新密码."""
    admin = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(name="u", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _patch(
        client,
        f"/api/v1/datasources/{ds.pk}",
        {"name": "renamed", "password": "newpwd"},
        _auth(admin),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "renamed"
    ds.refresh_from_db()
    assert ds.get_password() == "newpwd"


@pytest.mark.django_db
def test_update_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 更新应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    ds = DataSource.objects.create(name="u", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _patch(client, f"/api/v1/datasources/{ds.pk}", {"name": "v"}, _auth(viewer))
    assert response.status_code == 403


@pytest.mark.django_db
def test_update_duplicate_name_returns_400(make_user: Callable[..., User]) -> None:
    """更新为已存在名称应返回 400."""
    admin = make_user(role=Role.ADMIN)
    DataSource.objects.create(name="other", engine=EngineType.SQLITE, database=":memory:")
    ds = DataSource.objects.create(name="u", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _patch(client, f"/api/v1/datasources/{ds.pk}", {"name": "other"}, _auth(admin))
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_engine_and_without_password(make_user: Callable[..., User]) -> None:
    """更新引擎类型且不修改密码应成功."""
    admin = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(name="u", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _patch(
        client,
        f"/api/v1/datasources/{ds.pk}",
        {"engine": "mysql", "host": "h", "port": 3306, "database": "db"},
        _auth(admin),
    )
    assert response.status_code == 200
    ds.refresh_from_db()
    assert ds.engine == "mysql"
    assert ds.host == "h"


# ---------- 删除 ----------


@pytest.mark.django_db
def test_delete_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """管理员删除数据源应成功."""
    admin = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(name="d", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _delete(client, f"/api/v1/datasources/{ds.pk}", _auth(admin))
    assert response.status_code == 200
    assert not DataSource.objects.filter(pk=ds.pk).exists()


@pytest.mark.django_db
def test_delete_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 删除应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    ds = DataSource.objects.create(name="d", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _delete(client, f"/api/v1/datasources/{ds.pk}", _auth(viewer))
    assert response.status_code == 403


# ---------- 测试连接 ----------


@pytest.mark.django_db
def test_saved_connection_success(make_user: Callable[..., User]) -> None:
    """已保存 SQLite 内存库连接测试应成功（viewer 可测）."""
    user = make_user(role=Role.VIEWER)
    ds = DataSource.objects.create(name="ok", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    response = _post(client, f"/api/v1/datasources/{ds.pk}/test", None, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["ok"] is True


@pytest.mark.django_db
def test_temp_connection_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """管理员测试临时连接配置应成功."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources/test",
        {"engine": "sqlite", "database": ":memory:"},
        _auth(admin),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["ok"] is True


@pytest.mark.django_db
def test_temp_connection_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 测试临时连接应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources/test",
        {"engine": "sqlite", "database": ":memory:"},
        _auth(viewer),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_temp_connection_invalid_engine_returns_400(make_user: Callable[..., User]) -> None:
    """临时测试无效引擎应返回 400."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources/test",
        {"engine": "oracle", "database": "x"},
        _auth(admin),
    )
    assert response.status_code == 400
