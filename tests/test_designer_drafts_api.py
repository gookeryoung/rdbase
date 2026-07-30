"""designer 表设计器接口测试.

覆盖草稿 CRUD、版本管理、DDL 预览与执行接口。
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
from apps.designer.models import DesignDraft, DesignVersion, DraftStatus
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


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


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Iterator[None]:
    """每个测试后清空引擎缓存，避免缓存污染."""

    yield

    dispose_all()


def _make_sqlite_ds(tmp_path: Path, name: str = "sqlite-test") -> DataSource:
    """构造基于临时文件的空 SQLite 数据源（不预置表）."""
    db_path = tmp_path / "test.db"
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


def _make_spec_dict(
    *,
    name: str = "users",
    fields: list[dict[str, Any]] | None = None,
    indexes: list[dict[str, Any]] | None = None,
    foreign_keys: list[dict[str, Any]] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """构造测试用 spec dict."""
    if fields is None:
        fields = [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True, "autoincrement": True},
            {"name": "name", "type": "VARCHAR", "length": 50, "nullable": False},
        ]
    return {
        "name": name,
        "schema_name": None,
        "comment": comment,
        "fields": fields,
        "indexes": indexes or [],
        "foreign_keys": foreign_keys or [],
    }


# ---------- 草稿 CRUD ----------


@pytest.mark.django_db
def test_create_draft_returns_201(make_user: Callable[..., User], tmp_path: Path) -> None:
    """designer 创建草稿应返回 201 并生成首个版本."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "name": "users 表设计",
        "datasource_id": ds.pk,
        "table_name": "users",
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/drafts", body, _auth(user))
    assert response.status_code == 201
    data = json.loads(response.content)
    assert data["name"] == "users 表设计"
    assert data["datasource_id"] == ds.pk
    assert data["table_name"] == "users"
    assert data["status"] == DraftStatus.DRAFT
    # 应自动创建首个版本
    draft = DesignDraft.objects.get(pk=data["id"])
    assert draft.versions.count() == 1  # type: ignore[missing-attribute]
    assert draft.versions.first().version_no == 1  # type: ignore[missing-attribute]


@pytest.mark.django_db
def test_create_draft_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 创建草稿应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "name": "users",
        "datasource_id": ds.pk,
        "table_name": "users",
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/drafts", body, _auth(user))
    assert response.status_code == 403


@pytest.mark.django_db
def test_create_draft_duplicate_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """同一数据源同一表名重复创建草稿应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "name": "users",
        "datasource_id": ds.pk,
        "table_name": "users",
        "spec": _make_spec_dict(),
    }
    _post(client, "/api/v1/designer/drafts", body, _auth(user))
    response = _post(client, "/api/v1/designer/drafts", body, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_draft_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """数据源不存在应返回 404."""
    user = make_user(role=Role.DESIGNER)
    client = Client()
    body = {
        "name": "users",
        "datasource_id": 99999,
        "table_name": "users",
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/drafts", body, _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_list_drafts_returns_all(make_user: Callable[..., User], tmp_path: Path) -> None:
    """列表应返回所有草稿."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    # 创建 2 个草稿
    DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="t1",
        spec=_make_spec_dict(name="t1"),
    )
    DesignDraft.objects.create(
        name="draft2",
        datasource=ds,
        table_name="t2",
        spec=_make_spec_dict(name="t2"),
    )
    client = Client()
    response = _get(client, "/api/v1/designer/drafts", _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert len(data) == 2


@pytest.mark.django_db
def test_list_drafts_filter_by_datasource(make_user: Callable[..., User], tmp_path: Path) -> None:
    """按 datasource_id 过滤草稿列表."""
    user = make_user(role=Role.DESIGNER)
    ds1 = _make_sqlite_ds(tmp_path, name="ds1")
    ds2 = _make_sqlite_ds(tmp_path, name="ds2")
    DesignDraft.objects.create(
        name="draft1",
        datasource=ds1,
        table_name="t1",
        spec=_make_spec_dict(name="t1"),
    )
    DesignDraft.objects.create(
        name="draft2",
        datasource=ds2,
        table_name="t2",
        spec=_make_spec_dict(name="t2"),
    )
    client = Client()
    response = _get(client, f"/api/v1/designer/drafts?datasource_id={ds1.pk}", _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert len(data) == 1
    assert data[0]["datasource_id"] == ds1.pk


@pytest.mark.django_db
def test_retrieve_draft_returns_detail(make_user: Callable[..., User], tmp_path: Path) -> None:
    """详情应返回草稿完整信息."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _get(client, f"/api/v1/designer/drafts/{draft.pk}", _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["id"] == draft.pk
    assert data["name"] == "draft1"


@pytest.mark.django_db
def test_retrieve_draft_not_found_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的草稿应返回 404."""
    user = make_user(role=Role.DESIGNER)
    client = Client()
    response = _get(client, "/api/v1/designer/drafts/99999", _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_update_draft_creates_new_version(make_user: Callable[..., User], tmp_path: Path) -> None:
    """更新草稿应自动创建新版本."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    # 创建首个版本
    DesignVersion.objects.create(draft=draft, version_no=1, spec=draft.spec)
    client = Client()
    new_spec = _make_spec_dict(
        fields=[
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True, "autoincrement": True},
            {"name": "name", "type": "VARCHAR", "length": 100, "nullable": False},
        ]
    )
    response = _patch(
        client,
        f"/api/v1/designer/drafts/{draft.pk}",
        {"name": "updated", "spec": new_spec},
        _auth(user),
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["name"] == "updated"
    draft.refresh_from_db()
    assert draft.versions.count() == 2  # type: ignore[missing-attribute]
    assert draft.versions.order_by("-version_no").first().version_no == 2  # type: ignore[missing-attribute]


@pytest.mark.django_db
def test_update_draft_table_name_conflict_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """更新表名与其他草稿冲突应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="t1",
        spec=_make_spec_dict(name="t1"),
    )
    draft2 = DesignDraft.objects.create(
        name="draft2",
        datasource=ds,
        table_name="t2",
        spec=_make_spec_dict(name="t2"),
    )
    client = Client()
    response = _patch(
        client,
        f"/api/v1/designer/drafts/{draft2.pk}",
        {"table_name": "t1"},
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_draft_rename_table_name_success(make_user: Callable[..., User], tmp_path: Path) -> None:
    """更新表名（无冲突）应成功并持久化新表名."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    DesignVersion.objects.create(draft=draft, version_no=1, spec=draft.spec)
    client = Client()
    response = _patch(
        client,
        f"/api/v1/designer/drafts/{draft.pk}",
        {"table_name": "members", "schema_name": "public"},
        _auth(user),
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["table_name"] == "members"
    assert data["schema_name"] == "public"
    draft.refresh_from_db()
    assert draft.table_name == "members"
    assert draft.schema_name == "public"


@pytest.mark.django_db
def test_delete_draft_returns_200(make_user: Callable[..., User], tmp_path: Path) -> None:
    """删除草稿应级联删除版本."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    DesignVersion.objects.create(draft=draft, version_no=1, spec=draft.spec)
    client = Client()
    response = _delete(client, f"/api/v1/designer/drafts/{draft.pk}", _auth(user))
    assert response.status_code == 200
    assert not DesignDraft.objects.filter(pk=draft.pk).exists()
    assert not DesignVersion.objects.filter(draft_id=draft.pk).exists()


@pytest.mark.django_db
def test_delete_draft_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 删除草稿应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _delete(client, f"/api/v1/designer/drafts/{draft.pk}", _auth(user))
    assert response.status_code == 403


# ---------- 版本管理 ----------


@pytest.mark.django_db
def test_list_versions_returns_all(make_user: Callable[..., User], tmp_path: Path) -> None:
    """版本列表应返回所有版本（所有登录用户可读）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    DesignVersion.objects.create(draft=draft, version_no=1, spec=draft.spec)
    DesignVersion.objects.create(draft=draft, version_no=2, spec=draft.spec)
    client = Client()
    response = _get(client, f"/api/v1/designer/drafts/{draft.pk}/versions", _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert len(data) == 2
    # 应按版本号倒序
    assert data[0]["version_no"] == 2
    assert data[1]["version_no"] == 1


@pytest.mark.django_db
def test_rollback_to_version_creates_new_version(make_user: Callable[..., User], tmp_path: Path) -> None:
    """回滚版本应把指定版本 spec 作为新版本保存."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(comment="v1"),
    )
    v1 = DesignVersion.objects.create(draft=draft, version_no=1, spec={"name": "users", "comment": "v1"})
    # 更新 spec 到 v2
    draft.spec = _make_spec_dict(comment="v2")
    draft.save()
    DesignVersion.objects.create(draft=draft, version_no=2, spec=draft.spec)

    # 回滚到 v1
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/versions/{v1.version_no}/rollback",
        None,
        _auth(user),
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    # 应创建 v3，spec 与 v1 一致
    draft.refresh_from_db()
    assert draft.versions.count() == 3  # type: ignore[missing-attribute]
    assert draft.spec == v1.spec
    assert data["spec"] == v1.spec


@pytest.mark.django_db
def test_rollback_unknown_version_returns_404(make_user: Callable[..., User], tmp_path: Path) -> None:
    """回滚到不存在的版本应返回 404."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/versions/999/rollback",
        None,
        _auth(user),
    )
    assert response.status_code == 404


# ---------- DDL 预览 ----------


@pytest.mark.django_db
def test_preview_ddl_create(make_user: Callable[..., User], tmp_path: Path) -> None:
    """DDL 预览应返回 CREATE TABLE 语句（不传 old_spec）."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "datasource_id": ds.pk,
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/ddl/preview", body, _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert len(data["statements"]) > 0
    assert data["statements"][0].startswith('CREATE TABLE "users"')


@pytest.mark.django_db
def test_preview_ddl_alter(make_user: Callable[..., User], tmp_path: Path) -> None:
    """DDL 预览传入 old_spec 应返回 ALTER 语句."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "datasource_id": ds.pk,
        "spec": _make_spec_dict(name="members"),
        "old_spec": _make_spec_dict(name="users"),
    }
    response = _post(client, "/api/v1/designer/ddl/preview", body, _auth(user))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert any("RENAME TO" in s for s in data["statements"])


@pytest.mark.django_db
def test_preview_ddl_unknown_datasource_returns_404(make_user: Callable[..., User]) -> None:
    """DDL 预览数据源不存在应返回 404."""
    user = make_user(role=Role.VIEWER)
    client = Client()
    body = {
        "datasource_id": 99999,
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/ddl/preview", body, _auth(user))
    assert response.status_code == 404


@pytest.mark.django_db
def test_preview_ddl_invalid_spec_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """DDL 预览非法 spec（空字段）应返回 400."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    client = Client()
    body = {
        "datasource_id": ds.pk,
        "spec": _make_spec_dict(fields=[]),
    }
    response = _post(client, "/api/v1/designer/ddl/preview", body, _auth(user))
    assert response.status_code == 400


@pytest.mark.django_db
def test_preview_ddl_unauthenticated_returns_401() -> None:
    """未认证访问 DDL 预览应返回 401."""
    client = Client()
    body = {
        "datasource_id": 1,
        "spec": _make_spec_dict(),
    }
    response = _post(client, "/api/v1/designer/ddl/preview", body)
    assert response.status_code == 401


# ---------- DDL 执行 ----------


@pytest.mark.django_db
def test_apply_draft_creates_table_in_target_ds(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应用草稿应在目标数据源中创建表，并标记草稿为 applied."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(user),
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["executed"] > 0
    # 草稿状态应为 applied
    draft.refresh_from_db()
    assert draft.status == DraftStatus.APPLIED
    # 验证表已创建
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    try:
        table_names = sa_inspect(engine).get_table_names()
        assert "users" in table_names
    finally:
        engine.dispose()


@pytest.mark.django_db
def test_apply_draft_viewer_returns_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """viewer 应用草稿应返回 403."""
    user = make_user(role=Role.VIEWER)
    ds = _make_sqlite_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(user),
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_apply_draft_unknown_returns_404(make_user: Callable[..., User]) -> None:
    """应用不存在的草稿应返回 404."""
    user = make_user(role=Role.DESIGNER)
    client = Client()
    response = _post(
        client,
        "/api/v1/designer/drafts/99999/apply",
        {},
        _auth(user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_apply_draft_execution_failure_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """DDL 执行失败（如表已存在）应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    # 先创建旧表
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_apply_draft_ddl_error_returns_400(make_user: Callable[..., User], tmp_path: Path) -> None:
    """草稿 spec 非法（空字段）导致 DDL 生成失败应返回 400."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    # 直接构造非法 spec（字段列表为空）
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(fields=[]),
    )
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(user),
    )
    assert response.status_code == 400
    data = json.loads(response.content)
    assert "至少需要一个字段" in data["detail"]


@pytest.mark.django_db
def test_apply_draft_with_old_spec_alters_table(make_user: Callable[..., User], tmp_path: Path) -> None:
    """应用草稿传入 old_spec 应执行 ALTER 而非 CREATE."""
    user = make_user(role=Role.DESIGNER)
    ds = _make_sqlite_ds(tmp_path)
    # 先创建旧表
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL)"))
    engine.dispose()

    # 草稿 spec 与旧表结构相同（无变更）
    draft = DesignDraft.objects.create(
        name="draft1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
    )
    client = Client()
    # 传入与当前表结构一致的 old_spec，应无 ALTER 语句
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {"old_spec": _make_spec_dict()},
        _auth(user),
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    # 无变更时 executed=0
    assert data["executed"] == 0
