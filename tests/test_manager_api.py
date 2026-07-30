"""manager 数据浏览与 CRUD 接口测试.

通过临时文件 SQLite 数据源验证 rows API 端到端行为。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.engine import dispose_all
from apps.datasources.models import DataSource, EngineType
from apps.manager.query import QueryError
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def _get(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """发送 GET 请求."""
    h = headers or {}
    return cast(HttpResponse, client.get(url, **h))


def _post(
    client: Client,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 POST 请求."""
    h = headers or {}
    data = json.dumps(body) if body is not None else None
    return cast(HttpResponse, client.post(url, data=data, content_type="application/json", **h))


def _patch(
    client: Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 PATCH 请求."""
    h = headers or {}
    data = json.dumps(body)
    return cast(HttpResponse, client.patch(url, data=data, content_type="application/json", **h))


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


def _make_sqlite_file_ds(tmp_path: Path, name: str = "sqlite-test") -> DataSource:
    """构造基于临时文件的 SQLite 数据源并预置表结构与数据."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100), "
                "age INTEGER DEFAULT 0"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, name, email, age) VALUES "
                "(1, 'Alice', 'alice@example.com', 30), "
                "(2, 'Bob', 'bob@example.com', 25), "
                "(3, 'Charlie', 'charlie@example.com', 35)"
            )
        )
        # 创建一张空表，覆盖无数据时 columns 回退分支
        conn.execute(text("CREATE TABLE empty_table (id INTEGER PRIMARY KEY, label VARCHAR(50))"))
        # 多列主键表（覆盖 P4-2 多列主键 CRUD 路径）
        conn.execute(
            text(
                "CREATE TABLE composite (a INTEGER NOT NULL, b INTEGER NOT NULL, name VARCHAR(50), PRIMARY KEY (a, b))"
            )
        )
        conn.execute(text("INSERT INTO composite (a, b, name) VALUES (1, 100, 'first')"))
    engine.dispose()
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


# ---------- 默认查询 ----------


@pytest.mark.django_db
def test_list_rows_returns_all(make_user: Callable[..., User], tmp_path: Path) -> None:
    """默认应返回所有行、所有列（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/tables/users/rows", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert set(body["columns"]) == {"id", "name", "email", "age"}
    assert len(body["items"]) == 3
    assert body["items"][0]["name"] == "Alice"


@pytest.mark.django_db
def test_list_rows_pagination(make_user: Callable[..., User], tmp_path: Path) -> None:
    """分页参数应正确生效."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?page=1&page_size=2",
        _auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert [item["id"] for item in body["items"]] == [1, 2]


@pytest.mark.django_db
def test_list_rows_order_by(make_user: Callable[..., User], tmp_path: Path) -> None:
    """排序参数应正确生效."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?order_by=age&order_dir=desc",
        _auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    ages = [item["age"] for item in body["items"]]
    assert ages == [35, 30, 25]


@pytest.mark.django_db
def test_list_rows_columns_subset(make_user: Callable[..., User], tmp_path: Path) -> None:
    """columns 参数应控制返回列."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?columns=id,name",
        _auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["columns"] == ["id", "name"]
    assert set(body["items"][0].keys()) == {"id", "name"}


@pytest.mark.django_db
def test_list_rows_filter_eq(make_user: Callable[..., User], tmp_path: Path) -> None:
    """eq 筛选应正确生效."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    filters_json = json.dumps({"name": {"op": "eq", "val": "Alice"}})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows?filters={quote(filters_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Alice"


@pytest.mark.django_db
def test_list_rows_filter_like(make_user: Callable[..., User], tmp_path: Path) -> None:
    """like 模糊筛选应正确生效."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    filters_json = json.dumps({"email": {"op": "like", "val": "%@example.com"}})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows?filters={quote(filters_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3


@pytest.mark.django_db
def test_list_rows_filter_in(make_user: Callable[..., User], tmp_path: Path) -> None:
    """in 筛选应正确生效."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    filters_json = json.dumps({"id": {"op": "in", "val": [1, 3]}})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows?filters={quote(filters_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {1, 3}


@pytest.mark.django_db
def test_list_rows_empty_table_returns_columns(make_user: Callable[..., User], tmp_path: Path) -> None:
    """空表应返回空 items 但 columns 仍为表的所有列."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/empty_table/rows",
        _auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 0
    assert body["items"] == []
    assert set(body["columns"]) == {"id", "label"}


# ---------- 错误分支 ----------


@pytest.mark.django_db
def test_list_rows_without_token_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/manager/1/tables/users/rows")
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_rows_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    response = _get(
        client,
        "/api/v1/manager/99999/tables/users/rows",
        _auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_list_rows_unknown_table_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """不存在的表应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/nonexistent/rows",
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_invalid_column_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非法列名应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?columns=nonexistent",
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_invalid_order_by_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非法排序字段应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?order_by=nonexistent",
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_invalid_filters_json_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非法 filters JSON 应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows?filters=not-a-json",
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_invalid_filter_op_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非法筛选操作符应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    filters_json = json.dumps({"name": {"op": "contains", "val": "A"}})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows?filters={quote(filters_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_filters_not_object_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """filters 为合法 JSON 但非对象时应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    filters_json = json.dumps([1, 2, 3])
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows?filters={quote(filters_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查询过程中抛 SQLAlchemyError 应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> tuple[list[dict[str, Any]], int]:
        raise SQLAlchemyError("connection lost")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.query_table_rows", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/tables/users/rows", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_rows_empty_table_reflect_error_returns_empty_columns(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空表且反射列名失败时应返回空 columns（覆盖 get_column_names 异常分支）."""

    def _raise_query_error(*_args: object, **_kwargs: object) -> list[str]:
        raise QueryError("reflect failed")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    # 让 query_table_rows 正常返回空行（空表场景）
    monkeypatch.setattr(
        "apps.manager.api.query_table_rows",
        lambda *_a, **_kw: ([], 0),
    )
    # 让 get_column_names 抛 QueryError（覆盖 except 分支）
    monkeypatch.setattr("apps.manager.api.get_column_names", _raise_query_error)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/tables/empty_table/rows",
        _auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["items"] == []
    assert body["total"] == 0
    assert body["columns"] == []


# ---------- P4-2 行 CRUD：POST 新增 ----------


@pytest.mark.django_db
def test_create_row_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 新增行应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"name": "X", "email": "x@example.com", "age": 1}},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_create_row_without_token_returns_401(tmp_path: Path) -> None:
    """未认证访问应返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"name": "X"}},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_create_row_designer_returns_201(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 新增行应返回 201 且主键回填."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"name": "Dan", "email": "dan@example.com", "age": 22}},
        headers=_auth(user),
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["row"]["name"] == "Dan"
    assert body["row"]["email"] == "dan@example.com"
    assert body["row"]["age"] == 22
    assert isinstance(body["row"]["id"], int)
    assert body["row"]["id"] > 0


@pytest.mark.django_db
def test_create_row_admin_returns_201(make_user: Callable[..., User], tmp_path: Path) -> None:
    """admin 新增行应返回 201."""
    user = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"name": "Admin", "email": None, "age": 0}},
        headers=_auth(user),
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_create_row_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user(role=Role.DESIGNER)
    client = Client()
    response = _post(
        client,
        "/api/v1/manager/99999/tables/users/rows",
        body={"values": {"name": "X"}},
        headers=_auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_create_row_invalid_column_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非法列名应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"nonexistent": "x"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_row_composite_pk_returns_201(make_user: Callable[..., User], tmp_path: Path) -> None:
    """多列主键表显式提供主键列应返回 201."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/composite/rows",
        body={"values": {"a": 2, "b": 200, "name": "second"}},
        headers=_auth(user),
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["row"]["a"] == 2
    assert body["row"]["b"] == 200
    assert body["row"]["name"] == "second"


# ---------- P4-2 行 CRUD：GET 单行 ----------


@pytest.mark.django_db
def test_retrieve_row_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """按主键查单行应返回 200."""
    user = make_user()  # viewer 可读
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["row"]["id"] == 1
    assert body["row"]["name"] == "Alice"


@pytest.mark.django_db
def test_retrieve_row_not_exists_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """行不存在应返回 404."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 9999})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_retrieve_row_missing_pk_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """缺 pk 参数应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_invalid_pk_json_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """pk 非法 JSON 应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk=not-a-json"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_pk_not_object_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """pk 为合法 JSON 但非对象应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps([1, 2, 3])
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_composite_pk_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """多列主键查单行应返回 200."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"a": 1, "b": 100})
    url = f"/api/v1/manager/{ds.pk}/tables/composite/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["row"]["name"] == "first"


@pytest.mark.django_db
def test_retrieve_row_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/99999/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 404


# ---------- P4-2 行 CRUD：PATCH 更新 ----------


@pytest.mark.django_db
def test_update_row_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 更新行应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "Alice2"}},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_update_row_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 更新行应返回 200 且返回更新后的行."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "Alice2", "age": 31}},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["row"]["id"] == 1
    assert body["row"]["name"] == "Alice2"
    assert body["row"]["age"] == 31
    # email 应保持不变
    assert body["row"]["email"] == "alice@example.com"


@pytest.mark.django_db
def test_update_row_not_exists_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """行不存在应返回 404（乐观锁冲突）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 9999})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "ghost"}},
        headers=_auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_update_row_pk_in_values_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """主键列出现在 values 中应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"id": 2, "name": "x"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_row_missing_pk_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """缺 pk 参数应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk"
    response = _patch(
        client,
        url,
        body={"values": {"name": "x"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_row_composite_pk_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """多列主键更新应返回 200."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"a": 1, "b": 100})
    url = f"/api/v1/manager/{ds.pk}/tables/composite/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "updated"}},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["row"]["name"] == "updated"


# ---------- P4-2 行 CRUD：DELETE 删除 ----------


@pytest.mark.django_db
def test_delete_row_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 删除行应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 403


@pytest.mark.django_db
def test_delete_row_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 删除行应返回 200."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["detail"] == "已删除"
    # 数据应已删除
    rows_url = f"/api/v1/manager/{ds.pk}/tables/users/rows"
    rows_resp = _get(client, rows_url, _auth(user))
    rows_body = json.loads(rows_resp.content)
    assert rows_body["total"] == 2
    assert all(r["id"] != 1 for r in rows_body["items"])


@pytest.mark.django_db
def test_delete_row_not_exists_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """行不存在应返回 404（乐观锁冲突）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"id": 9999})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_delete_row_missing_pk_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """缺 pk 参数应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_row_composite_pk_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """多列主键删除应返回 200."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({"a": 1, "b": 100})
    url = f"/api/v1/manager/{ds.pk}/tables/composite/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["detail"] == "已删除"


@pytest.mark.django_db
def test_create_row_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新增过程中抛 SQLAlchemyError 应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise SQLAlchemyError("connection lost")

    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.insert_row", _raise_sqlalchemy_error)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/users/rows",
        body={"values": {"name": "X"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_row_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """更新过程中抛 SQLAlchemyError 应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise SQLAlchemyError("connection lost")

    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.update_row", _raise_sqlalchemy_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "x"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_row_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除过程中抛 SQLAlchemyError 应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("connection lost")

    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.delete_row", _raise_sqlalchemy_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查询过程中抛 SQLAlchemyError 应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise SQLAlchemyError("connection lost")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.get_row", _raise_sqlalchemy_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_pk_empty_object_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """pk 为空对象 {} 应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    pk_json = json.dumps({})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_row_query_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_row 抛 QueryError 时应返回 400（覆盖 retrieve_row 的 QueryError 分支）."""

    def _raise_query_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise QueryError("表不存在")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.get_row", _raise_query_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _get(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_row_query_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_row 抛非「不存在」QueryError 时应返回 400（覆盖 delete 分支 400 路径）."""

    def _raise_query_error(*_args: object, **_kwargs: object) -> None:
        raise QueryError("主键不能为空")

    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.delete_row", _raise_query_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _delete(client, url, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_row_query_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """update_row 抛非「不存在」QueryError 时应返回 400（覆盖 update 分支 400 路径）."""

    def _raise_query_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise QueryError("主键不能为空")

    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.update_row", _raise_query_error)
    client = Client()
    pk_json = json.dumps({"id": 1})
    url = f"/api/v1/manager/{ds.pk}/tables/users/rows/pk?pk={quote(pk_json)}"
    response = _patch(
        client,
        url,
        body={"values": {"name": "x"}},
        headers=_auth(user),
    )
    assert response.status_code == 400


# ============================================================
# P4-3 SQL 查询控制台 - /query 接口
# ============================================================


@pytest.mark.django_db
def test_execute_sql_select_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 执行 SELECT 应返回 200 + 结果集."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "SELECT id, name FROM users WHERE id = 1"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["read_only"] is True
    assert body["columns"] == ["id", "name"]
    assert len(body["rows"]) == 1
    assert body["rows"][0]["name"] == "Alice"
    assert body["rowcount"] == 1
    assert body["elapsed_ms"] >= 0


@pytest.mark.django_db
def test_execute_sql_select_trailing_semicolon(make_user: Callable[..., User], tmp_path: Path) -> None:
    """末尾分号应被正确处理."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "SELECT COUNT(*) AS cnt FROM users;"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["rows"][0]["cnt"] == 3


@pytest.mark.django_db
def test_execute_sql_insert_by_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 执行 INSERT 应返回 200 + rowcount."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "INSERT INTO users (name, email, age) VALUES ('Dan', 'd@e.com', 33)"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["read_only"] is False
    assert body["rowcount"] == 1
    assert body["columns"] == []
    assert body["rows"] == []


@pytest.mark.django_db
def test_execute_sql_insert_by_admin_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """admin 执行 INSERT 应返回 200."""
    user = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "INSERT INTO users (name, email, age) VALUES ('Eve', 'e@e.com', 22)"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["rowcount"] == 1


@pytest.mark.django_db
def test_execute_sql_insert_by_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 执行 INSERT 应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "INSERT INTO users (name) VALUES ('blocked')"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_execute_sql_update_by_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 执行 UPDATE 应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "UPDATE users SET age = 1 WHERE id = 1"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_execute_sql_delete_by_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 执行 DELETE 应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "DELETE FROM users WHERE id = 1"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_execute_sql_ddl_by_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 执行 DDL DROP 应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "DROP TABLE users"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_execute_sql_ddl_by_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 执行 DDL CREATE TABLE 应返回 200 + rowcount=-1."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "CREATE TABLE foo (id INTEGER PRIMARY KEY)"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["rowcount"] == -1


@pytest.mark.django_db
def test_execute_sql_without_token_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = _post(client, "/api/v1/manager/1/query", body={"sql": "SELECT 1"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_execute_sql_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    response = _post(
        client,
        "/api/v1/manager/99999/query",
        body={"sql": "SELECT 1"},
        headers=_auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_execute_sql_empty_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """空 SQL 应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": ""},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_execute_sql_syntax_error_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQL 语法错误应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "SELECT FROM WHERE"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_execute_sql_write_committed(make_user: Callable[..., User], tmp_path: Path) -> None:
    """DML 写入后应能在新连接读到（事务已提交）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "INSERT INTO users (name, email, age) VALUES ('Gina', 'g@e.com', 28)"},
        headers=_auth(user),
    )
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        body={"sql": "SELECT COUNT(*) AS cnt FROM users"},
        headers=_auth(user),
    )
    body = json.loads(response.content)
    assert body["rows"][0]["cnt"] == 4


# ============================================================
# P4-3 SQL 查询控制台 - /explain 接口
# ============================================================


@pytest.mark.django_db
def test_explain_sql_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 调用 EXPLAIN 应返回 200 + 计划."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": "SELECT * FROM users WHERE id = 1"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["dialect"] == "sqlite"
    assert body["analyze"] is False
    assert isinstance(body["plan"], list)
    assert len(body["plan"]) > 0
    assert "detail" in body["rows"][0]


@pytest.mark.django_db
def test_explain_sql_analyze_ignored_on_sqlite(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 应忽略 analyze 参数."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": "SELECT * FROM users", "analyze": True},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["analyze"] is False


@pytest.mark.django_db
def test_explain_sql_without_token_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = _post(client, "/api/v1/manager/1/explain", body={"sql": "SELECT 1"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_explain_sql_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    response = _post(
        client,
        "/api/v1/manager/99999/explain",
        body={"sql": "SELECT 1"},
        headers=_auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_explain_sql_empty_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """空 SQL 应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": ""},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_explain_sql_syntax_error_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQL 语法错误应返回 400."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": "SELECT FROM WHERE"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_explain_sql_query_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """explain_sql 抛 QueryError 时应返回 400（覆盖 explain QueryError 分支）."""

    def _raise_query_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise QueryError("方言不支持")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.explain_sql", _raise_query_error)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": "SELECT 1"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_explain_sql_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """explain_sql 抛 SQLAlchemyError 时应返回 400."""

    def _raise_sqlalchemy_error(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise SQLAlchemyError("connection lost")

    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.manager.api.explain_sql", _raise_sqlalchemy_error)
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/explain",
        body={"sql": "SELECT 1"},
        headers=_auth(user),
    )
    assert response.status_code == 400
