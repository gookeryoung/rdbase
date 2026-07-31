"""manager 对象管理接口端到端测试.

通过临时文件 SQLite 数据源验证 views/routines/triggers 接口的端到端行为。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.engine import dispose_all
from apps.datasources.models import DataSource, EngineType
from apps.manager import api as manager_api
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


def _put(
    client: Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 PUT 请求."""
    h = headers or {}
    data = json.dumps(body)
    return cast(HttpResponse, client.put(url, data=data, content_type="application/json", **h))


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


def _make_sqlite_file_ds(tmp_path: Path, name: str = "sqlite-objects") -> DataSource:
    """构造基于临时文件的 SQLite 数据源并预置表/视图/触发器."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "age INTEGER DEFAULT 0"
                ")"
            )
        )
        conn.execute(text("INSERT INTO users (name, age) VALUES ('Alice', 30)"))
        # 视图
        conn.execute(text("CREATE VIEW adult_view AS SELECT id, name FROM users WHERE age >= 18"))
        # 触发器
        conn.execute(
            text(
                "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users "
                "FOR EACH ROW WHEN NEW.age < 0 BEGIN SELECT RAISE(ABORT, 'age 不能为负'); END"
            )
        )
    engine.dispose()
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


# ============================================================
# 视图接口
# ============================================================


@pytest.mark.django_db
def test_list_views_returns_views(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应返回数据源中的视图列表（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    names = [item["name"] for item in body]
    assert "adult_view" in names


@pytest.mark.django_db
def test_list_views_without_token_returns_401(tmp_path: Path) -> None:
    """未认证访问视图列表应返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views")
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_views_unknown_ds_returns_404(make_user: Callable[..., User]) -> None:
    """未知数据源应返回 404."""
    user = make_user(role=Role.VIEWER)
    client = Client()
    response = _get(client, "/api/v1/manager/99999/views", _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_retrieve_view_returns_definition(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应返回视图定义 SQL."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views/adult_view", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "adult_view"
    assert body["schema_name"] is None
    assert "SELECT" in body["definition"]


@pytest.mark.django_db
def test_retrieve_view_nonexistent_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """不存在的视图应返回 404."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views/nonexistent", _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_update_view_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 编辑视图应返回 200 + 新定义."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    new_def = "CREATE VIEW adult_view AS SELECT id, name, age FROM users WHERE age >= 18"
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        body={"definition": new_def},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "age" in body["definition"]


@pytest.mark.django_db
def test_update_view_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 编辑视图应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    new_def = "CREATE VIEW adult_view AS SELECT id FROM users"
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        body={"definition": new_def},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_update_view_invalid_prefix_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非 CREATE VIEW 语句应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        body={"definition": "SELECT * FROM users"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_view_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 删除视图应返回 200."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        headers=_auth(user),
    )
    assert response.status_code == 200
    # 删除后查询应 404
    response2 = _get(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        headers=_auth(user),
    )
    assert response2.status_code == 404


@pytest.mark.django_db
def test_delete_view_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 删除视图应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        headers=_auth(user),
    )
    assert response.status_code == 403


# ============================================================
# 存储过程/函数接口（SQLite 不支持）
# ============================================================


@pytest.mark.django_db
def test_list_routines_sqlite_returns_empty(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 数据源 routines 应返回空列表."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/routines", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == []


@pytest.mark.django_db
def test_retrieve_routine_sqlite_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 获取 routine 定义应返回 400（不支持）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_routine_sqlite_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 删除 routine 应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_routine_sqlite_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 编辑 routine 应返回 400（不支持）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        body={"definition": "CREATE FUNCTION my_func() RETURNS INT RETURN 1"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_routine_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 编辑 routine 应返回 403（权限拦截先于方言检查）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        body={"definition": "CREATE FUNCTION my_func() RETURNS INT RETURN 1"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_update_routine_without_token_returns_401(tmp_path: Path) -> None:
    """未认证编辑 routine 应返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        body={"definition": "CREATE FUNCTION my_func() RETURNS INT RETURN 1"},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_retrieve_routine_without_token_returns_401(tmp_path: Path) -> None:
    """未认证获取 routine 定义应返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_routines_unknown_ds_returns_404(make_user: Callable[..., User]) -> None:
    """未知数据源 routines 列表应返回 404."""
    user = make_user(role=Role.VIEWER)
    client = Client()
    response = _get(client, "/api/v1/manager/99999/routines", _auth(user))
    assert response.status_code == 404


# ============================================================
# 触发器接口
# ============================================================


@pytest.mark.django_db
def test_list_triggers_returns_triggers(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应返回触发器列表（viewer 可读）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/triggers", _auth(user))
    assert response.status_code == 200
    body = json.loads(response.content)
    names = [item["name"] for item in body]
    assert "trg_before_insert" in names


@pytest.mark.django_db
def test_list_triggers_without_token_returns_401(tmp_path: Path) -> None:
    """未认证访问触发器列表应返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/triggers")
    assert response.status_code == 401


@pytest.mark.django_db
def test_retrieve_trigger_returns_definition(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应返回触发器定义 SQL."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "trg_before_insert"
    assert body["table"] == "users"
    assert "CREATE TRIGGER" in body["definition"].upper()


@pytest.mark.django_db
def test_retrieve_trigger_nonexistent_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """不存在的触发器应返回 404."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/nonexistent",
        headers=_auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_update_trigger_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 编辑触发器应返回 200 + 新定义."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    new_def = (
        "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users "
        "FOR EACH ROW WHEN NEW.age > 200 BEGIN SELECT RAISE(ABORT, 'age 超出范围'); END"
    )
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        body={"definition": new_def, "table": "users"},
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "age 超出范围" in body["definition"]


@pytest.mark.django_db
def test_update_trigger_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 编辑触发器应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    new_def = (
        "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users "
        "FOR EACH ROW WHEN NEW.age > 200 BEGIN SELECT RAISE(ABORT, 'age 超出范围'); END"
    )
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        body={"definition": new_def, "table": "users"},
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_update_trigger_invalid_prefix_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非 CREATE TRIGGER 语句应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        body={"definition": "SELECT 1", "table": "users"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_trigger_designer_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 删除触发器应返回 200."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert?table=users",
        headers=_auth(user),
    )
    assert response.status_code == 200
    # 删除后查询应 404
    response2 = _get(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        headers=_auth(user),
    )
    assert response2.status_code == 404


@pytest.mark.django_db
def test_delete_trigger_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 删除触发器应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert?table=users",
        headers=_auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_delete_trigger_sqlite_no_table_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """SQLite 删除触发器无需 table 参数（与 PG 不同）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        headers=_auth(user),
    )
    assert response.status_code == 200


# ============================================================
# SQLAlchemyError 分支测试（monkeypatch objects 函数）
# ============================================================


def _raise_sqlalchemy_error(*args: Any, **kwargs: Any) -> Any:
    """抛 SQLAlchemyError 的 mock 函数."""
    raise SQLAlchemyError("mock error")


@pytest.mark.django_db
def test_list_views_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_views 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "list_views", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_view_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_view_definition 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "get_view_definition", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/views/adult_view", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_view_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """alter_view 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "alter_view", _raise_sqlalchemy_error)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        body={"definition": "CREATE VIEW adult_view AS SELECT id FROM users"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_view_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_view 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "drop_view", _raise_sqlalchemy_error)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/views/adult_view",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_routines_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_routines 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "list_routines", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/routines", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_routine_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_routine_definition 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "get_routine_definition", _raise_sqlalchemy_error)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_routine_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """alter_routine 抛 SQLAlchemyError 应返回 400（SQLite 不支持先抛 ObjectError，需 mock 为 mysql）.

    用 monkeypatch 替换 _resolve_obj_schema 让方言判定为 mysql，再让 alter_routine 抛 SQLAlchemyError。
    """
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    # 强制走非 SQLite 分支，避开 ObjectError
    monkeypatch.setattr(manager_api, "_resolve_obj_schema", lambda _ds, s: s or None)
    monkeypatch.setattr(manager_api, "alter_routine", _raise_sqlalchemy_error)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        body={"definition": "CREATE FUNCTION my_func() RETURNS INT RETURN 1"},
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_routine_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_routine 抛 SQLAlchemyError 应返回 400（SQLite 不支持先抛 ObjectError，需 mock 为 mysql）."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "_resolve_obj_schema", lambda _ds, s: s or None)
    monkeypatch.setattr(manager_api, "drop_routine", _raise_sqlalchemy_error)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/routines/my_func?type=function",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_triggers_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_triggers 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "list_triggers", _raise_sqlalchemy_error)
    client = Client()
    response = _get(client, f"/api/v1/manager/{ds.pk}/triggers", _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_retrieve_trigger_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retrieve_trigger 中 list_triggers 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "list_triggers", _raise_sqlalchemy_error)
    client = Client()
    response = _get(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_trigger_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """alter_trigger 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "alter_trigger", _raise_sqlalchemy_error)
    client = Client()
    response = _put(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert",
        body={
            "definition": "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users FOR EACH ROW BEGIN END",
            "table": "users",
        },
        headers=_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_trigger_sqlalchemy_error_returns_400(
    make_user: Callable[..., User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_trigger 抛 SQLAlchemyError 应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_file_ds(tmp_path)
    monkeypatch.setattr(manager_api, "drop_trigger", _raise_sqlalchemy_error)
    client = Client()
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/triggers/trg_before_insert?table=users",
        headers=_auth(user),
    )
    assert response.status_code == 400
