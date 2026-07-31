"""审计日志集成测试.

验证各业务模块（datasources/designer/manager）写操作后能正确记录业务级审计日志：
- 字段填充（action/resource_type/datasource_id 等）
- 失败路径也记录
- source=BUSINESS
- 双层审计：中间件 + 业务层各记录一条
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import quote

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.audit.models import AuditAction, AuditLog, AuditSource, AuditStatus
from apps.datasources.engine import dispose_all
from apps.datasources.models import DataSource, EngineType
from apps.designer.models import DesignDraft, DraftStatus
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


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


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> None:
    """每个测试后清空引擎缓存."""

    dispose_all()


def _make_sqlite_file_ds(tmp_path: Path, name: str = "audit-test") -> DataSource:
    """构造基于临时文件的 SQLite 数据源并预置表结构."""
    db_path = tmp_path / "audit_test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, name VARCHAR(50))"))
    engine.dispose()
    return DataSource.objects.create(name=name, engine=EngineType.SQLITE, database=str(db_path))


def _make_spec_dict() -> dict[str, object]:
    """构造一个简单的表设计规范（用于 DDL apply）."""
    return {
        "name": "users",
        "fields": [
            {
                "name": "id",
                "type": "INTEGER",
                "nullable": False,
                "primary_key": True,
                "unique": False,
                "autoincrement": True,
            },
            {
                "name": "name",
                "type": "VARCHAR",
                "length": 50,
                "nullable": False,
                "primary_key": False,
                "unique": False,
                "autoincrement": False,
            },
        ],
        "indexes": [],
        "foreign_keys": [],
    }


# ---------- datasources ----------


@pytest.mark.django_db
def test_datasource_create_logs_business_audit(
    make_user: Callable[..., User],
) -> None:
    """创建数据源应记录一条 source=BUSINESS 的 DATASOURCE_CREATE 审计."""
    admin = make_user(role=Role.ADMIN)
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_CREATE).count()
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "audit-create", "engine": "sqlite", "database": ":memory:"},
        _auth(admin),
    )
    assert response.status_code == 201
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_CREATE).count()
    assert after - before == 1
    log = (
        AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_CREATE)
        .order_by("-id")
        .first()
    )
    assert log is not None
    assert log.resource_type == "datasource"
    assert log.username == admin.username
    assert log.extra.get("name") == "audit-create"


@pytest.mark.django_db
def test_datasource_update_logs_business_audit(
    make_user: Callable[..., User],
) -> None:
    """更新数据源应记录 DATASOURCE_UPDATE."""
    admin = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(name="upd-test", engine=EngineType.SQLITE, database=":memory:")
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_UPDATE).count()
    client = Client()
    response = _patch(
        client,
        f"/api/v1/datasources/{ds.pk}",
        {"tags": ["new-tag"]},
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_UPDATE).count()
    assert after - before == 1
    log = (
        AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_UPDATE)
        .order_by("-id")
        .first()
    )
    assert log is not None
    assert log.resource_type == "datasource"
    assert log.resource_id == str(ds.pk)


@pytest.mark.django_db
def test_datasource_delete_logs_business_audit(
    make_user: Callable[..., User],
) -> None:
    """删除数据源应记录 DATASOURCE_DELETE."""
    admin = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(name="del-test", engine=EngineType.SQLITE, database=":memory:")
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_DELETE).count()
    client = Client()
    response = _delete(client, f"/api/v1/datasources/{ds.pk}", _auth(admin))
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DATASOURCE_DELETE).count()
    assert after - before == 1


# ---------- designer ----------


@pytest.mark.django_db
def test_designer_apply_ddl_logs_business_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """应用 DDL 应记录 DDL_APPLY 审计日志（含 SQL）."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    draft = DesignDraft.objects.create(
        name="d1",
        datasource=ds,
        table_name="users",
        spec=_make_spec_dict(),
        status=DraftStatus.DRAFT,
    )
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DDL_APPLY).count()
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DDL_APPLY).count()
    assert after - before == 1
    log = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DDL_APPLY).order_by("-id").first()
    assert log is not None
    assert log.status == AuditStatus.SUCCESS
    assert log.datasource_id == ds.pk
    assert log.sql  # SQL 文本非空


@pytest.mark.django_db
def test_designer_apply_ddl_failure_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """应用 DDL 失败时也应记录 DDL_APPLY，status=FAILURE."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    # 预置同名表，使 CREATE TABLE 失败
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE dup_t (id INTEGER)"))
    engine.dispose()
    draft = DesignDraft.objects.create(
        name="d2",
        datasource=ds,
        table_name="dup_t",  # 重名 → CREATE 失败
        spec={
            "name": "dup_t",
            "fields": [
                {
                    "name": "id",
                    "type": "INTEGER",
                    "nullable": False,
                    "primary_key": True,
                    "unique": False,
                    "autoincrement": False,
                }
            ],
            "indexes": [],
            "foreign_keys": [],
        },
        status=DraftStatus.DRAFT,
    )
    before = AuditLog.objects.filter(
        source=AuditSource.BUSINESS,
        action=AuditAction.DDL_APPLY,
        status=AuditStatus.FAILURE,
    ).count()
    client = Client()
    response = _post(
        client,
        f"/api/v1/designer/drafts/{draft.pk}/apply",
        {},
        _auth(admin),
    )
    assert response.status_code == 400
    after = AuditLog.objects.filter(
        source=AuditSource.BUSINESS,
        action=AuditAction.DDL_APPLY,
        status=AuditStatus.FAILURE,
    ).count()
    assert after - before == 1


# ---------- manager ----------


@pytest.mark.django_db
def test_manager_create_row_logs_business_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """新增行应记录 DML_INSERT 审计."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_INSERT).count()
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/tables/t/rows",
        {"values": {"id": 1, "name": "alice"}},
        _auth(admin),
    )
    assert response.status_code == 201
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_INSERT).count()
    assert after - before == 1
    log = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_INSERT).order_by("-id").first()
    assert log is not None
    assert log.status == AuditStatus.SUCCESS
    assert log.datasource_id == ds.pk
    assert log.row_count == 1


@pytest.mark.django_db
def test_manager_update_row_logs_business_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """更新行应记录 DML_UPDATE 审计."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    # 先插入一行
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t (id, name) VALUES (1, 'old')"))
    engine.dispose()
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_UPDATE).count()
    client = Client()
    pk_json = quote(json.dumps({"id": 1}))
    response = _patch(
        client,
        f"/api/v1/manager/{ds.pk}/tables/t/rows/pk?pk={pk_json}",
        {"values": {"name": "new"}},
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_UPDATE).count()
    assert after - before == 1


@pytest.mark.django_db
def test_manager_delete_row_logs_business_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """删除行应记录 DML_DELETE 审计."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    engine = create_engine(f"sqlite:///{ds.database}", future=True)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t (id, name) VALUES (1, 'to-del')"))
    engine.dispose()
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_DELETE).count()
    client = Client()
    pk_json = quote(json.dumps({"id": 1}))
    response = _delete(
        client,
        f"/api/v1/manager/{ds.pk}/tables/t/rows/pk?pk={pk_json}",
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.DML_DELETE).count()
    assert after - before == 1


@pytest.mark.django_db
def test_manager_execute_sql_write_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """执行写 SQL 应记录 SQL_EXECUTE 审计（含 elapsed_ms）."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.SQL_EXECUTE).count()
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        {"sql": "INSERT INTO t (id, name) VALUES (100, 'x')"},
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.SQL_EXECUTE).count()
    assert after - before == 1
    log = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.SQL_EXECUTE).order_by("-id").first()
    assert log is not None
    assert log.elapsed_ms is not None and log.elapsed_ms >= 0
    assert log.datasource_id == ds.pk


@pytest.mark.django_db
def test_manager_execute_sql_read_only_not_logged(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """只读 SELECT 不应记录业务级 SQL_EXECUTE 审计."""
    admin = make_user(role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    before = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.SQL_EXECUTE).count()
    client = Client()
    response = _post(
        client,
        f"/api/v1/manager/{ds.pk}/query",
        {"sql": "SELECT 1"},
        _auth(admin),
    )
    assert response.status_code == 200
    after = AuditLog.objects.filter(source=AuditSource.BUSINESS, action=AuditAction.SQL_EXECUTE).count()
    assert after == before


# ---------- 双层审计：中间件 + 业务层 ----------


@pytest.mark.django_db
def test_dual_layer_audit_for_datasource_create(
    make_user: Callable[..., User],
) -> None:
    """数据源创建应同时产生中间件层与业务层两条审计记录."""
    admin = make_user(role=Role.ADMIN)
    before_mw = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    before_biz = AuditLog.objects.filter(source=AuditSource.BUSINESS).count()
    client = Client()
    response = _post(
        client,
        "/api/v1/datasources",
        {"name": "dual-test", "engine": "sqlite", "database": ":memory:"},
        _auth(admin),
    )
    assert response.status_code == 201
    after_mw = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    after_biz = AuditLog.objects.filter(source=AuditSource.BUSINESS).count()
    assert after_mw - before_mw == 1
    assert after_biz - before_biz == 1
