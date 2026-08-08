"""数据源管理 Router.

- 列表/详情：所有登录用户可访问
- 创建/更新/删除/测试临时连接：仅 admin
- 测试已保存数据源连接：所有登录用户
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.models import User
from apps.accounts.permissions import require_admin
from apps.audit.audit import log_audit
from apps.audit.models import AuditAction

from .engine import dispose_engine, verify_connection
from .models import DataSource, EngineType
from .scanner import scan_sqlite_files
from .schemas import (
    DataSourceCreateIn,
    DataSourceOut,
    DataSourceUpdateIn,
    MessageOut,
    ScanResultOut,
    TestConnectionIn,
    TestConnectionOut,
)

router = Router(tags=["datasources"], auth=JWTAuth())


def _ds_dict(ds: DataSource) -> dict[str, Any]:
    """构造数据源响应字典."""
    return {
        "id": ds.pk,
        "name": ds.name,
        "engine": ds.engine,
        "host": ds.host,
        "port": ds.port,
        "database": ds.database,
        "username": ds.username,
        "group": ds.group,
        "tags": ds.tags,
        "is_active": ds.is_active,
        "created_at": ds.created_at.isoformat(),  # type: ignore[missing-attribute]
        "updated_at": ds.updated_at.isoformat(),  # type: ignore[missing-attribute]
    }


def _get_ds_or_404(ds_id: int) -> DataSource:
    """按 ID 获取数据源，不存在抛 404."""
    try:
        return DataSource.objects.get(pk=ds_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "数据源不存在") from None


def _validate_engine(engine: str) -> None:
    """校验引擎类型合法."""
    if engine not in EngineType.values:
        raise HttpError(400, "引擎类型无效")


@router.get("", response={200: list[DataSourceOut]})
def list_datasources(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    """获取数据源列表（所有登录用户）."""
    items = DataSource.objects.all().order_by("-id")
    body = [DataSourceOut(**_ds_dict(d)).model_dump() for d in items]
    return JsonResponse(body, safe=False)


@router.post("", response={201: DataSourceOut})
def create_datasource(request: HttpRequest, payload: DataSourceCreateIn) -> HttpResponse:
    """创建数据源（仅管理员）."""
    require_admin(request)
    _validate_engine(payload.engine)
    if DataSource.objects.filter(name=payload.name).exists():
        raise HttpError(400, "数据源名称已存在")
    user = cast(User, getattr(request, "auth", None))
    ds = DataSource(
        name=payload.name,
        engine=payload.engine,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        group=payload.group,
        tags=payload.tags,
        created_by=user,
    )
    if payload.password:
        ds.set_password(payload.password)
    ds.save()
    log_audit(
        request,
        action=AuditAction.DATASOURCE_CREATE,
        resource_type="datasource",
        resource_id=str(ds.pk),
        extra={"name": ds.name, "engine": ds.engine},
    )
    body = DataSourceOut(**_ds_dict(ds)).model_dump()
    return JsonResponse(body, status=201)


@router.post("/test", response={200: TestConnectionOut})
def test_temp_datasource(request: HttpRequest, payload: TestConnectionIn) -> HttpResponse:
    """测试未保存的临时连接配置（仅管理员）."""
    require_admin(request)
    _validate_engine(payload.engine)
    # 构造临时 DataSource 实例（不入库）用于 verify_connection
    ds = DataSource(
        name="temp",
        engine=payload.engine,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
    )
    ds.set_password(payload.password)
    ok, detail = verify_connection(ds)
    return JsonResponse({"ok": ok, "detail": detail})


@router.post("/scan", response={200: ScanResultOut})
def scan_datasources(request: HttpRequest, directory: str | None = None) -> HttpResponse:
    """扫描本地 SQLite 数据库文件并注册为数据源（仅管理员）.

    可通过 ``directory`` 查询参数指定扫描目录，默认使用 ``DATA_DIR``。
    """
    require_admin(request)
    target = Path(directory) if directory else None
    result = scan_sqlite_files(target)
    log_audit(
        request,
        action=AuditAction.DATASOURCE_SCAN,
        resource_type="datasource",
        extra={
            "directory": str(result.directory),
            "scanned": result.scanned,
            "created": [ds.name for ds in result.created],
            "skipped_count": len(result.skipped),
        },
    )
    body = {
        "directory": str(result.directory),
        "scanned": result.scanned,
        "created": [DataSourceOut(**_ds_dict(ds)).model_dump() for ds in result.created],
        "skipped": result.skipped,
    }
    return JsonResponse(body)


@router.get("/{ds_id}", response={200: DataSourceOut})
def retrieve_datasource(request: HttpRequest, ds_id: int) -> HttpResponse:  # noqa: ARG001
    """获取数据源详情（所有登录用户）."""
    ds = _get_ds_or_404(ds_id)
    body = DataSourceOut(**_ds_dict(ds)).model_dump()
    return JsonResponse(body)


@router.patch("/{ds_id}", response={200: DataSourceOut})
def update_datasource(request: HttpRequest, ds_id: int, payload: DataSourceUpdateIn) -> HttpResponse:
    """更新数据源（仅管理员）."""
    require_admin(request)
    ds = _get_ds_or_404(ds_id)
    data = payload.model_dump(exclude_unset=True)
    if "engine" in data:
        _validate_engine(data["engine"])
    if "name" in data and data["name"] != ds.name and DataSource.objects.filter(name=data["name"]).exists():
        raise HttpError(400, "数据源名称已存在")
    password = data.pop("password", None)
    for key, value in data.items():
        setattr(ds, key, value)  # type: ignore[bad-assignment]
    if password:
        ds.set_password(password)
    # 更新后旧引擎缓存应失效
    dispose_engine(ds.pk)
    ds.save()
    log_audit(
        request,
        action=AuditAction.DATASOURCE_UPDATE,
        resource_type="datasource",
        resource_id=str(ds.pk),
        extra={"name": ds.name, "engine": ds.engine},
    )
    body = DataSourceOut(**_ds_dict(ds)).model_dump()
    return JsonResponse(body)


@router.delete("/{ds_id}", response={200: MessageOut})
def delete_datasource(request: HttpRequest, ds_id: int) -> HttpResponse:
    """删除数据源（仅管理员）."""
    require_admin(request)
    ds = _get_ds_or_404(ds_id)
    ds_name = ds.name
    ds_engine = ds.engine
    dispose_engine(ds.pk)
    ds.delete()
    log_audit(
        request,
        action=AuditAction.DATASOURCE_DELETE,
        resource_type="datasource",
        resource_id=str(ds_id),
        extra={"name": ds_name, "engine": ds_engine},
    )
    return JsonResponse({"detail": "已删除"})


@router.post("/{ds_id}/test", response={200: TestConnectionOut})
def test_saved_datasource(request: HttpRequest, ds_id: int) -> HttpResponse:  # noqa: ARG001
    """测试已保存数据源的连接（所有登录用户）."""
    ds = _get_ds_or_404(ds_id)
    ok, detail = verify_connection(ds)
    return JsonResponse({"ok": ok, "detail": detail})
