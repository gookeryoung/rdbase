"""manager 数据浏览接口测试.

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
