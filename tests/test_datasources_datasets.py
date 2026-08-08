"""数据集（Dataset）模型与管理/查询 API 端到端测试.

覆盖：

- 模型：``Dataset`` 创建/``__str__``/``increment_version``/Meta ordering+indexes。
- 管理 CRUD（``JWTAuth`` + admin）：创建/列表/详情/更新（version 自增）/删除/
  非 admin 403/未认证 401/slug 重复 400/审计日志记录。
- 公开查询（``ApiTokenAuth`` + ``datasets:read`` scope）：``X-API-Token`` 头、
  ``Bearer`` 头、无 token 401、无 scope 403、``is_active=False`` 404、
  分页/排序/字段裁剪/过滤/``filter_expression`` 强制行级过滤（同名列防绕过）/
  ``fields_whitelist`` 列级权限（请求非白名单列 400）/预览端点（``JWTAuth``+admin）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import ApiToken, Role, User
from apps.audit.models import AuditAction, AuditLog
from apps.datasources.engine import dispose_all
from apps.datasources.models import Dataset, DataSource, EngineType
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text

# ================================================================
# 模型测试
# ================================================================


@pytest.mark.django_db
def test_dataset_defaults(make_user: Callable[..., User]) -> None:
    """Dataset 默认值：is_active=True、version=1、空 list/dict、schema_name=''."""
    user = make_user(role=Role.ADMIN)
    ds = DataSource.objects.create(
        name="ds-defaults",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="user-profiles",
        name="用户资料",
        datasource=ds,
        table_name="users",
        owner=user,
    )
    assert dataset.slug == "user-profiles"
    assert dataset.name == "用户资料"
    assert dataset.description == ""
    assert dataset.schema_name == ""
    assert dataset.fields_whitelist == []
    assert dataset.filter_expression == {}
    assert dataset.aggregations == {}
    assert dataset.is_active is True
    assert dataset.version == 1
    assert dataset.owner_id == user.pk
    assert dataset.created_at is not None
    assert dataset.updated_at is not None


@pytest.mark.django_db
def test_dataset_str_representation() -> None:
    """__str__ 应为 'slug (name)' 格式."""
    ds = DataSource.objects.create(
        name="ds-str",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="orders",
        name="订单数据集",
        datasource=ds,
        table_name="orders",
    )
    assert str(dataset) == "orders (订单数据集)"


@pytest.mark.django_db
def test_dataset_increment_version() -> None:
    """increment_version 应使 version +1（每次调用累加）."""
    ds = DataSource.objects.create(
        name="ds-inc",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="metrics",
        name="指标",
        datasource=ds,
        table_name="metrics",
    )
    assert dataset.version == 1
    dataset.increment_version()
    assert dataset.version == 2
    dataset.increment_version()
    assert dataset.version == 3


@pytest.mark.django_db
def test_dataset_increment_version_from_zero() -> None:
    """version 为 0/None 时 increment_version 按 1 起算再 +1（防御场景）."""
    ds = DataSource.objects.create(
        name="ds-zero",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="zero-ver",
        name="零版本",
        datasource=ds,
        table_name="t",
        version=0,
    )
    # (0 or 1) + 1 = 2：0 视为缺失，按默认 1 起算再自增
    dataset.increment_version()
    assert dataset.version == 2


def test_dataset_meta_ordering() -> None:
    """Meta.ordering 应为 -id."""
    assert Dataset._meta.ordering == ["-id"]  # type: ignore[missing-attribute]


def test_dataset_meta_indexes() -> None:
    """模型应包含 3 个索引（active/datasource/owner）."""
    index_names = {idx.name for idx in Dataset._meta.indexes}  # type: ignore[missing-attribute]
    assert index_names == {"idx_dataset_active", "idx_dataset_ds", "idx_dataset_owner"}


def test_dataset_slug_unique() -> None:
    """slug 字段应有 unique 约束."""
    slug_field = Dataset._meta.get_field("slug")  # type: ignore[missing-attribute]
    assert slug_field.unique is True  # type: ignore[union-attr]


@pytest.mark.django_db
def test_dataset_cascade_on_datasource_delete() -> None:
    """数据源删除时关联数据集应级联删除（on_delete=CASCADE）."""
    ds = DataSource.objects.create(
        name="ds-cascade",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="cascade-test",
        name="级联",
        datasource=ds,
        table_name="t",
    )
    ds.delete()
    assert not Dataset.objects.filter(pk=dataset.pk).exists()


@pytest.mark.django_db
def test_dataset_owner_set_null_on_user_delete(make_user: Callable[..., User]) -> None:
    """owner 删除时数据集 owner_id 应置 NULL（on_delete=SET_NULL）."""
    user = make_user(username="owner1", role=Role.ADMIN)
    ds = DataSource.objects.create(
        name="ds-owner",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    dataset = Dataset.objects.create(
        slug="with-owner",
        name="带负责人",
        datasource=ds,
        table_name="t",
        owner=user,
    )
    user.delete()
    dataset.refresh_from_db()
    assert dataset.owner_id is None


# ================================================================
# 端到端测试：辅助与 fixture
# ================================================================


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Iterator[None]:
    """每个测试后清空引擎缓存，避免缓存污染."""

    yield

    dispose_all()


def _make_sqlite_file_ds(tmp_path: Path, name: str = "ds-sqlite") -> DataSource:
    """构造基于临时文件的 SQLite 数据源并预置 users 表与 3 行数据."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100), "
                "age INTEGER DEFAULT 0, "
                "is_active INTEGER DEFAULT 1"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, name, email, age, is_active) VALUES "
                "(1, 'Alice', 'alice@example.com', 30, 1), "
                "(2, 'Bob', 'bob@example.com', 25, 1), "
                "(3, 'Charlie', 'charlie@example.com', 35, 0)"
            )
        )
    engine.dispose()
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


def _make_dataset(  # noqa: PLR0913
    ds: DataSource,
    *,
    slug: str = "user-profiles",
    name: str = "用户资料",
    table_name: str = "users",
    fields_whitelist: list[str] | None = None,
    filter_expression: dict[str, Any] | None = None,
    is_active: bool = True,
    **kwargs: Any,
) -> Dataset:
    """创建 Dataset 实例（默认绑定 users 表）."""
    return Dataset.objects.create(
        slug=slug,
        name=name,
        datasource=ds,
        table_name=table_name,
        fields_whitelist=fields_whitelist or [],
        filter_expression=filter_expression or {},
        is_active=is_active,
        **kwargs,
    )


def _auth_client(user: User) -> Client:
    """构造携带 JWT 的客户端（默认头方式）."""
    token = create_access_token(user.pk, str(user.role))
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


def _token_client(plaintext: str, *, use_bearer: bool = False) -> Client:
    """构造携带 ApiToken 的客户端.

    默认用 ``X-API-Token`` 头；``use_bearer=True`` 时改用 ``Authorization: Bearer``。
    """
    if use_bearer:
        return Client(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    return Client(HTTP_X_API_TOKEN=plaintext)


def _get(client: Client, url: str) -> HttpResponse:
    """发送 GET 请求."""
    return cast(HttpResponse, client.get(url))


def _post(client: Client, url: str, body: dict[str, object]) -> HttpResponse:
    """发送 POST 请求（JSON 体）."""
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json"),
    )


def _patch(client: Client, url: str, body: dict[str, object]) -> HttpResponse:
    """发送 PATCH 请求（JSON 体）."""
    return cast(
        HttpResponse,
        client.patch(url, data=json.dumps(body), content_type="application/json"),
    )


def _delete(client: Client, url: str) -> HttpResponse:
    """发送 DELETE 请求."""
    return cast(HttpResponse, client.delete(url))


def _make_token(
    user: User,
    *,
    scopes: list[str] | None = None,
    name: str = "ci-token",
) -> tuple[str, ApiToken]:
    """生成 ApiToken 明文与实例."""
    return ApiToken.generate(
        name=name,
        created_by=user,
        scopes=scopes if scopes is not None else ["datasets:read"],
    )


# ================================================================
# 管理 CRUD（JWTAuth + require_admin）
# ================================================================


@pytest.mark.django_db
def test_admin_list_datasets_empty(make_user: Callable[..., User]) -> None:
    """管理员列表：空库返回 items=[] total=0."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"items": [], "total": 0}


@pytest.mark.django_db
def test_admin_create_dataset_returns_201(make_user: Callable[..., User], tmp_path: Path) -> None:
    """管理员创建数据集应返回 201 + 完整字段."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    client = _auth_client(admin)
    response = _post(
        client,
        "/api/v1/datasets",
        {
            "slug": "user-profiles",
            "name": "用户资料",
            "datasource_id": ds.pk,
            "table_name": "users",
        },
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["slug"] == "user-profiles"
    assert body["name"] == "用户资料"
    assert body["datasource_id"] == ds.pk
    assert body["table_name"] == "users"
    assert body["is_active"] is True
    assert body["version"] == 1
    assert body["owner_id"] == admin.pk
    assert body["fields_whitelist"] == []
    assert body["filter_expression"] == {}
    assert body["aggregations"] == {}


@pytest.mark.django_db
def test_admin_create_dataset_with_extras(make_user: Callable[..., User], tmp_path: Path) -> None:
    """创建时可携带白名单/过滤条件等可选字段."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    client = _auth_client(admin)
    response = _post(
        client,
        "/api/v1/datasets",
        {
            "slug": "active-users",
            "name": "活跃用户",
            "description": "仅活跃用户",
            "datasource_id": ds.pk,
            "table_name": "users",
            "fields_whitelist": ["id", "name", "email"],
            "filter_expression": {"is_active": 1},
            "aggregations": {"count": {"op": "count"}},
            "is_active": True,
        },
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["description"] == "仅活跃用户"
    assert body["fields_whitelist"] == ["id", "name", "email"]
    assert body["filter_expression"] == {"is_active": 1}
    assert body["aggregations"] == {"count": {"op": "count"}}


@pytest.mark.django_db
def test_admin_create_dataset_dup_slug_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """slug 重复创建返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="dup-slug")
    client = _auth_client(admin)
    response = _post(
        client,
        "/api/v1/datasets",
        {"slug": "dup-slug", "name": "重复", "datasource_id": ds.pk, "table_name": "users"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_create_dataset_invalid_datasource_400(
    make_user: Callable[..., User],
) -> None:
    """datasource_id 不存在返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = _auth_client(admin)
    response = _post(
        client,
        "/api/v1/datasets",
        {"slug": "x", "name": "x", "datasource_id": 9999, "table_name": "users"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_create_dataset_non_admin_403(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非管理员创建返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    client = _auth_client(viewer)
    response = _post(
        client,
        "/api/v1/datasets",
        {"slug": "x", "name": "x", "datasource_id": ds.pk, "table_name": "users"},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_create_dataset_unauth_401(tmp_path: Path) -> None:
    """未认证创建返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasets",
        {"slug": "x", "name": "x", "datasource_id": ds.pk, "table_name": "users"},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_admin_retrieve_dataset(make_user: Callable[..., User], tmp_path: Path) -> None:
    """管理员按 slug 查详情，含 is_active=False 的."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="inactive-ds", name="未启用", is_active=False)
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/inactive-ds")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["slug"] == "inactive-ds"
    assert body["is_active"] is False


@pytest.mark.django_db
def test_admin_retrieve_dataset_not_found_404(make_user: Callable[..., User]) -> None:
    """slug 不存在返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/no-such-slug")
    assert response.status_code == 404


@pytest.mark.django_db
def test_admin_update_dataset_version_increment(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """更新数据集后 version 自增 1."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="upd", name="原名")
    client = _auth_client(admin)
    response = _patch(client, "/api/v1/datasets/upd", {"name": "新名"})
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["name"] == "新名"
    assert body["version"] == 2


@pytest.mark.django_db
def test_admin_update_dataset_slug_collision_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """更新 slug 到已存在 slug 返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="ds-a", name="A")
    _make_dataset(ds, slug="ds-b", name="B")
    client = _auth_client(admin)
    response = _patch(client, "/api/v1/datasets/ds-a", {"slug": "ds-b"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_update_dataset_invalid_datasource_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """更新 datasource_id 到不存在 ID 返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="upd-ds")
    client = _auth_client(admin)
    response = _patch(client, "/api/v1/datasets/upd-ds", {"datasource_id": 9999})
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_delete_dataset(make_user: Callable[..., User], tmp_path: Path) -> None:
    """删除数据集返回 200 + MessageOut."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="del-me")
    client = _auth_client(admin)
    response = _delete(client, "/api/v1/datasets/del-me")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "del-me" in body["detail"]
    assert not Dataset.objects.filter(slug="del-me").exists()


@pytest.mark.django_db
def test_admin_delete_non_admin_403(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """非管理员删除返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="del-viewer")
    client = _auth_client(viewer)
    response = _delete(client, "/api/v1/datasets/del-viewer")
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_create_dataset_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """创建数据集应记录 DATASET_CREATE 审计日志."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    client = _auth_client(admin)
    _post(
        client,
        "/api/v1/datasets",
        {"slug": "audit-create", "name": "审计", "datasource_id": ds.pk, "table_name": "users"},
    )
    log = AuditLog.objects.filter(
        action=AuditAction.DATASET_CREATE,
        source="business",
    ).first()
    assert log is not None
    assert log.resource_type == "dataset"
    assert log.extra.get("slug") == "audit-create"


@pytest.mark.django_db
def test_admin_update_dataset_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """更新数据集应记录 DATASET_UPDATE 审计日志且 extra.version >=2."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="audit-upd")
    client = _auth_client(admin)
    _patch(client, "/api/v1/datasets/audit-upd", {"name": "新名"})
    log = AuditLog.objects.filter(action=AuditAction.DATASET_UPDATE).first()
    assert log is not None
    assert log.extra.get("version") == 2


@pytest.mark.django_db
def test_admin_delete_dataset_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """删除数据集应记录 DATASET_DELETE 审计日志."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="audit-del")
    client = _auth_client(admin)
    _delete(client, "/api/v1/datasets/audit-del")
    log = AuditLog.objects.filter(action=AuditAction.DATASET_DELETE).first()
    assert log is not None
    assert log.extra.get("slug") == "audit-del"


# ================================================================
# 公开查询端点 /rows（ApiTokenAuth + datasets:read）
# ================================================================


@pytest.mark.django_db
def test_query_rows_with_x_api_token_header(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """X-API-Token 头携带有效 Token + scope 可查询."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-x-token")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-x-token/rows")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert {"id", "name", "email", "age", "is_active"} == set(body["columns"])


@pytest.mark.django_db
def test_query_rows_with_bearer_header(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """Bearer 头携带 ApiToken 明文也可查询."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-bearer")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext, use_bearer=True)
    response = _get(client, "/api/v1/datasets/rows-bearer/rows")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3


@pytest.mark.django_db
def test_query_rows_no_token_401(tmp_path: Path) -> None:
    """无 Token 查询返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-no-token")
    client = Client()
    response = _get(client, "/api/v1/datasets/rows-no-token/rows")
    assert response.status_code == 401


@pytest.mark.django_db
def test_query_rows_no_scope_403(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """Token 无 datasets:read scope 返回 403."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-no-scope")
    plaintext, _ = _make_token(admin, scopes=[])
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-no-scope/rows")
    assert response.status_code == 403


@pytest.mark.django_db
def test_query_rows_jwt_rejected_401(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """JWT（非 ApiToken）访问公开端点返回 401（ApiTokenAuth 按 token_hash 查表失败）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-jwt")
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/rows-jwt/rows")
    assert response.status_code == 401


@pytest.mark.django_db
def test_query_rows_inactive_dataset_404(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """is_active=False 的数据集公开查询返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="inactive", is_active=False)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/inactive/rows")
    assert response.status_code == 404


@pytest.mark.django_db
def test_query_rows_not_found_404(
    make_user: Callable[..., User],
) -> None:
    """slug 不存在返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/no-such/rows")
    assert response.status_code == 404


@pytest.mark.django_db
def test_query_rows_pagination(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """page/page_size 分页参数生效."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-page")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-page/rows?page=1&page_size=2")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


@pytest.mark.django_db
def test_query_rows_order_by(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """order_by/order_dir 排序生效."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-order")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-order/rows?order_by=age&order_dir=desc")
    assert response.status_code == 200
    body = json.loads(response.content)
    ages = [item["age"] for item in body["items"]]
    assert ages == [35, 30, 25]


@pytest.mark.django_db
def test_query_rows_columns_subset(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """columns 参数控制返回列（无白名单时自由选择）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-cols")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-cols/rows?columns=id,name")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["columns"] == ["id", "name"]
    assert set(body["items"][0].keys()) == {"id", "name"}


@pytest.mark.django_db
def test_query_rows_user_filters(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """filters 参数（标准 {op,val} 格式）过滤生效."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-filter")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    filters_param = json.dumps({"age": {"op": "ge", "val": 30}})
    response = _get(
        client,
        f"/api/v1/datasets/rows-filter/rows?filters={filters_param}",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 2  # Alice(30) + Charlie(35)
    assert {item["name"] for item in body["items"]} == {"Alice", "Charlie"}


@pytest.mark.django_db
def test_query_rows_filter_expression_simple_form(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """filter_expression 简写形式 ``{"col": val}`` 应规范化为 eq 比较."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-fe-simple", filter_expression={"is_active": 1})
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-fe-simple/rows")
    assert response.status_code == 200
    body = json.loads(response.content)
    # 仅 is_active=1 的行（Alice、Bob）
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"Alice", "Bob"}


@pytest.mark.django_db
def test_query_rows_filter_expression_normalized_form(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """filter_expression 已是 {op,val} 格式时原样生效."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(
        ds,
        slug="rows-fe-norm",
        filter_expression={"age": {"op": "ge", "val": 30}},
    )
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-fe-norm/rows")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"Alice", "Charlie"}


@pytest.mark.django_db
def test_query_rows_filter_expression_overrides_user(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """同名列以 Dataset.filter_expression 为准（防绕过）.

    Dataset 配置 is_active=1；用户尝试传 is_active=0 绕过，应仍以 Dataset 为准。
    """
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-fe-override", filter_expression={"is_active": 1})
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    filters_param = json.dumps({"is_active": {"op": "eq", "val": 0}})
    response = _get(
        client,
        f"/api/v1/datasets/rows-fe-override/rows?filters={filters_param}",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    # Dataset 配置 is_active=1 强制生效，用户尝试 is_active=0 被忽略
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"Alice", "Bob"}


@pytest.mark.django_db
def test_query_rows_filter_expression_combined_with_user(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """Dataset.filter_expression 与用户 filters 不同列时 AND 合并."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-fe-comb", filter_expression={"is_active": 1})
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    filters_param = json.dumps({"age": {"op": "ge", "val": 30}})
    response = _get(
        client,
        f"/api/v1/datasets/rows-fe-comb/rows?filters={filters_param}",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    # is_active=1 AND age>=30 → 仅 Alice
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Alice"


@pytest.mark.django_db
def test_query_rows_fields_whitelist_default(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """有白名单但用户未传 columns 时返回白名单列."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-wl-default", fields_whitelist=["id", "name"])
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-wl-default/rows")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["columns"] == ["id", "name"]
    assert set(body["items"][0].keys()) == {"id", "name"}


@pytest.mark.django_db
def test_query_rows_fields_whitelist_subset(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """白名单下用户请求白名单子集列应返回子集."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-wl-subset", fields_whitelist=["id", "name", "email"])
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-wl-subset/rows?columns=id")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["columns"] == ["id"]
    assert set(body["items"][0].keys()) == {"id"}


@pytest.mark.django_db
def test_query_rows_fields_whitelist_violation_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """用户请求非白名单列时返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-wl-violate", fields_whitelist=["id", "name"])
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-wl-violate/rows?columns=id,email")
    assert response.status_code == 400


@pytest.mark.django_db
def test_query_rows_invalid_filters_json_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """filters 非法 JSON 返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-bad-json")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-bad-json/rows?filters=not-json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_query_rows_invalid_column_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """请求不存在的列名返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rows-bad-col")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-bad-col/rows?columns=nonexistent")
    assert response.status_code == 400


@pytest.mark.django_db
def test_query_rows_datasource_inactive_404(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """数据源 is_active=False 时公开查询返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    ds.is_active = False
    ds.save()
    _make_dataset(ds, slug="rows-ds-inactive")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _get(client, "/api/v1/datasets/rows-ds-inactive/rows")
    assert response.status_code == 404


# ================================================================
# 预览端点 /preview（JWTAuth + require_admin）
# ================================================================


@pytest.mark.django_db
def test_preview_rows_admin(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """管理员预览返回数据集行（JWTAuth + admin）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-admin")
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/preview-admin/preview")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.django_db
def test_preview_rows_inactive_dataset_allowed(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """预览端点允许访问 is_active=False 的数据集."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-inactive", is_active=False)
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/preview-inactive/preview")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 3


@pytest.mark.django_db
def test_preview_rows_non_admin_403(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """非管理员预览返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-403")
    client = _auth_client(viewer)
    response = _get(client, "/api/v1/datasets/preview-403/preview")
    assert response.status_code == 403


@pytest.mark.django_db
def test_preview_rows_unauth_401(tmp_path: Path) -> None:
    """未认证预览返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-unauth")
    client = Client()
    response = _get(client, "/api/v1/datasets/preview-unauth/preview")
    assert response.status_code == 401


@pytest.mark.django_db
def test_preview_rows_with_filter_expression(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """预览也应用 Dataset.filter_expression（管理员可见过滤后的结果）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-fe", filter_expression={"is_active": 1})
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/preview-fe/preview")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"Alice", "Bob"}


@pytest.mark.django_db
def test_preview_rows_with_user_filters(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """预览端点支持用户 filters 参数."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-filter")
    client = _auth_client(admin)
    filters_param = json.dumps({"age": {"op": "eq", "val": 30}})
    response = _get(
        client,
        f"/api/v1/datasets/preview-filter/preview?filters={filters_param}",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Alice"


@pytest.mark.django_db
def test_preview_rows_columns_subset(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """预览端点支持 columns 参数."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="preview-cols")
    client = _auth_client(admin)
    response = _get(client, "/api/v1/datasets/preview-cols/preview?columns=name,email")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["columns"] == ["name", "email"]
    assert set(body["items"][0].keys()) == {"name", "email"}
