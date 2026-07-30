"""designer 接口测试.

通过临时文件 SQLite 数据源验证反射 API 端到端行为。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.engine import dispose_all
from apps.datasources.models import DataSource, EngineType
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
    """构造基于临时文件的 SQLite 数据源并预置表结构/索引/视图."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100)"
                ")"
            )
        )
        # 显式创建单列唯一索引（SQLite 反射会暴露此索引）
        conn.execute(text("CREATE UNIQUE INDEX idx_users_email ON users(email)"))
        conn.execute(
            text(
                "CREATE TABLE posts ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL, "
                "title VARCHAR(200), "
                "CONSTRAINT fk_posts_user FOREIGN KEY (user_id) REFERENCES users(id)"
                ")"
            )
        )
        conn.execute(text("CREATE INDEX idx_posts_user ON posts(user_id)"))
        conn.execute(text("CREATE VIEW active_users AS SELECT id, name FROM users"))
    engine.dispose()
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


# ---------- 数据库列表 ----------


@pytest.mark.django_db
def test_list_databases_returns_main(make_user: Callable[..., User], tmp_path: Path) -> None:
    """列数据库接口应返回 ['main']（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/databases", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == [{"name": "main"}]


@pytest.mark.django_db
def test_list_databases_unknown_ds_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的数据源应返回 404."""
    user = make_user()
    client = Client()
    response = _get(client, "/api/v1/designer/99999/databases", _auth(user))
    assert response.status_code == 404


# ---------- Schema 列表 ----------


@pytest.mark.django_db
def test_list_schemas_returns_main(make_user: Callable[..., User], tmp_path: Path) -> None:
    """Schema 列表接口应返回 ['main']."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/schemas", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == [{"name": "main"}]


# ---------- 表列表 ----------


@pytest.mark.django_db
def test_list_tables_returns_user_tables(make_user: Callable[..., User], tmp_path: Path) -> None:
    """表列表应返回所有用户表（不含视图），SQLite schema 字段为 None."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    names = {item["name"] for item in body}
    assert names == {"users", "posts"}
    assert all(item["schema_name"] is None for item in body)


@pytest.mark.django_db
def test_list_tables_with_schema_param(make_user: Callable[..., User], tmp_path: Path) -> None:
    """传入 schema 参数时 SQLite 仍应忽略并返回表（schema 字段强制 None）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables?schema_name=main", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert {item["name"] for item in body} == {"users", "posts"}


# ---------- 视图列表 ----------


@pytest.mark.django_db
def test_list_views_returns_views(make_user: Callable[..., User], tmp_path: Path) -> None:
    """视图列表应返回所有视图."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/views", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert {item["name"] for item in body} == {"active_users"}


# ---------- 表详情 ----------


@pytest.mark.django_db
def test_retrieve_table_returns_metadata(make_user: Callable[..., User], tmp_path: Path) -> None:
    """表详情应返回完整元数据（字段/主键/外键/索引）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables/posts", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "posts"
    assert body["schema_name"] is None
    col_names = [c["name"] for c in body["columns"]]
    assert col_names == ["id", "user_id", "title"]
    assert body["primary_key"] == ["id"]
    assert len(body["foreign_keys"]) == 1
    fk = body["foreign_keys"][0]
    assert fk["referred_table"] == "users"
    assert fk["columns"] == ["user_id"]
    idx_names = [i["name"] for i in body["indexes"]]
    assert "idx_posts_user" in idx_names


@pytest.mark.django_db
def test_retrieve_table_unique_column_flag(make_user: Callable[..., User], tmp_path: Path) -> None:
    """单列 UNIQUE 约束应合并到 column.unique."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables/users", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    email_col = next(c for c in body["columns"] if c["name"] == "email")
    assert email_col["unique"] is True


@pytest.mark.django_db
def test_retrieve_table_not_found_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """不存在的表应返回 404."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables/nonexistent", _auth(user))
    assert response.status_code == 404


# ---------- 认证 ----------


@pytest.mark.django_db
def test_list_without_token_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/designer/1/databases")
    assert response.status_code == 401


# ---------- 反射异常分支 ----------


def _raise_sqlalchemy_error(_engine: object, schema: str | None = None) -> list[str]:
    """抛 SQLAlchemyError 的 stub，用于 monkeypatch."""
    raise SQLAlchemyError("connect failed")


@pytest.mark.django_db
def test_list_databases_reflect_error_returns_400(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反射失败应返回 400（覆盖 list_databases_view except 分支）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.designer.api.list_databases", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/databases", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_schemas_reflect_error_returns_400(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反射失败应返回 400（覆盖 list_schemas_view except 分支）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.designer.api.list_schemas", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/schemas", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_tables_reflect_error_returns_400(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反射失败应返回 400（覆盖 list_tables_view except 分支）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.designer.api.list_tables", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/tables", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_views_reflect_error_returns_400(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反射失败应返回 400（覆盖 list_views_view except 分支）."""
    user = make_user()
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr("apps.designer.api.list_views", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/designer/{ds.pk}/views", _auth(user))
    assert response.status_code == 400


# ---------- _schema_for_response 非 SQLite 分支 ----------


@pytest.mark.django_db
def test_schema_for_response_non_sqlite_returns_schema() -> None:
    """非 SQLite 数据源应原样返回 schema（覆盖 _schema_for_response 非 SQLite 分支）."""
    from apps.designer.api import _schema_for_response

    ds = DataSource(name="mysql-ds", engine=EngineType.MYSQL, database="db")
    assert _schema_for_response(ds, "public") == "public"
    assert _schema_for_response(ds, None) is None
