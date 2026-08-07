"""designer 模块 Router - 元数据反射与表设计器接口.

P3-1 元数据反射：基于 SQLAlchemy inspect 提供库/Schema/表/字段元数据读取。
- 所有登录用户可读（admin/designer/viewer 均可浏览元数据）。

P3-2 表设计器：草稿 CRUD、版本管理、DDL 预览与执行。
- 草稿 CRUD/版本回滚/DDL 执行：designer 或 admin
- 版本列表/DDL 预览：所有登录用户可读
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError

from apps.accounts.auth import JWTAuth
from apps.accounts.models import User
from apps.accounts.permissions import require_designer_or_admin
from apps.audit.audit import log_audit
from apps.audit.models import AuditAction, AuditStatus
from apps.datasources.engine import get_engine
from apps.datasources.models import DataSource, EngineType

from .ddl import DDLError, generate_ddl
from .inspector import TableMeta, inspect_table, list_databases, list_schemas, list_tables, list_views
from .models import DesignDraft, DesignVersion, DraftStatus
from .schemas import (
    ColumnOut,
    DatabaseOut,
    DDLExecuteIn,
    DDLExecuteOut,
    DDLPreviewIn,
    DDLPreviewOut,
    DraftCreateIn,
    DraftOut,
    DraftUpdateIn,
    FieldSpec,
    ForeignKeyOut,
    ForeignKeySpec,
    IndexOut,
    IndexSpec,
    MessageOut,
    SchemaOut,
    TableBriefOut,
    TableDesignSpec,
    TableDetailOut,
    VersionOut,
)

router = Router(tags=["designer"], auth=JWTAuth())


# ============================================================
# P3-1 元数据反射接口
# ============================================================


def _get_ds_or_404(ds_id: int) -> DataSource:
    """按 ID 获取数据源，不存在抛 404."""
    try:
        return DataSource.objects.get(pk=ds_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "数据源不存在") from None


def _wrap_reflect_error(exc: Exception) -> HttpError:
    """将 SQLAlchemy 反射异常包装为 HttpError."""
    if isinstance(exc, NoSuchTableError):
        return HttpError(404, "表不存在")
    return HttpError(400, f"无法读取元数据: {exc}")


def _schema_for_response(ds: DataSource, schema: str | None) -> str | None:
    """响应中 schema 字段：SQLite 强制 None（无 schema 概念），其他方言原样返回."""
    if cast(str, ds.engine) == EngineType.SQLITE:
        return None
    return schema


def _table_detail_dict(meta: Any) -> dict[str, Any]:
    """将 TableMeta 转为响应字典."""
    return {
        "name": meta.name,
        "schema_name": meta.schema,
        "comment": meta.comment,
        "columns": [ColumnOut(**asdict(c)).model_dump() for c in meta.columns],
        "primary_key": list(meta.primary_key),
        "foreign_keys": [ForeignKeyOut(**asdict(f)).model_dump() for f in meta.foreign_keys],
        "indexes": [IndexOut(**asdict(i)).model_dump() for i in meta.indexes],
        "unique_constraints": [list(g) for g in meta.unique_constraints],
    }


@router.get("/{ds_id}/databases", response={200: list[DatabaseOut]})
def list_databases_view(request: HttpRequest, ds_id: int) -> HttpResponse:
    """列出数据源服务器上的所有数据库（所有登录用户）."""
    del request  # 仅认证用
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        names = list_databases(engine)
    except SQLAlchemyError as exc:
        raise _wrap_reflect_error(exc) from None
    body = [DatabaseOut(name=n).model_dump() for n in names]
    return JsonResponse(body, safe=False)


@router.get("/{ds_id}/schemas", response={200: list[SchemaOut]})
def list_schemas_view(request: HttpRequest, ds_id: int) -> HttpResponse:
    """列出当前数据库的 Schema 列表（所有登录用户）."""
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        names = list_schemas(engine)
    except SQLAlchemyError as exc:
        raise _wrap_reflect_error(exc) from None
    body = [SchemaOut(name=n).model_dump() for n in names]
    return JsonResponse(body, safe=False)


@router.get("/{ds_id}/tables", response={200: list[TableBriefOut]})
def list_tables_view(request: HttpRequest, ds_id: int, schema_name: str | None = None) -> HttpResponse:
    """列出指定 Schema 下的所有表（所有登录用户）."""
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        effective_schema = schema_name if schema_name else None
        names = list_tables(engine, schema=effective_schema)
        schema_out = _schema_for_response(ds, effective_schema)
    except SQLAlchemyError as exc:
        raise _wrap_reflect_error(exc) from None
    body = [TableBriefOut(name=n, schema_name=schema_out).model_dump() for n in names]
    return JsonResponse(body, safe=False)


@router.get("/{ds_id}/views", response={200: list[TableBriefOut]})
def list_views_view(request: HttpRequest, ds_id: int, schema_name: str | None = None) -> HttpResponse:
    """列出指定 Schema 下的所有视图（所有登录用户）."""
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        effective_schema = schema_name if schema_name else None
        names = list_views(engine, schema=effective_schema)
        schema_out = _schema_for_response(ds, effective_schema)
    except SQLAlchemyError as exc:
        raise _wrap_reflect_error(exc) from None
    body = [TableBriefOut(name=n, schema_name=schema_out).model_dump() for n in names]
    return JsonResponse(body, safe=False)


@router.get("/{ds_id}/tables/{table_name}", response={200: TableDetailOut})
def retrieve_table_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
) -> HttpResponse:
    """读取单张表的完整元数据（所有登录用户）."""
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        effective_schema = schema_name if schema_name else None
        meta = inspect_table(engine, table_name, schema=effective_schema)
    except (SQLAlchemyError, NoSuchTableError) as exc:
        raise _wrap_reflect_error(exc) from None
    body = TableDetailOut(**_table_detail_dict(meta)).model_dump()
    return JsonResponse(body)


@router.get("/{ds_id}/tables/{table_name}/reverse", response={200: TableDesignSpec})
def reverse_table_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
) -> HttpResponse:
    """反向工程：把已有表结构反射为 TableDesignSpec，供新建草稿导入（所有登录用户）.

    复用 ``_reflect_table_to_spec`` 的方言规整逻辑（主键非空、SQLite 隐式自增、
    自增主键 default 置 None、外键 on_delete 统一 RESTRICT），保证导入草稿后
    立即应用回库不会误判触发无意义 ALTER。
    """
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        effective_schema = schema_name if schema_name else None
        meta = inspect_table(engine, table_name, schema=effective_schema)
        spec = _reflect_table_to_spec(meta, cast(str, ds.engine))
    except (SQLAlchemyError, NoSuchTableError) as exc:
        raise _wrap_reflect_error(exc) from None
    body = spec.model_dump()
    return JsonResponse(body)


# ============================================================
# P3-2 表设计器接口
# ============================================================


def _get_draft_or_404(draft_id: int) -> DesignDraft:
    """按 ID 获取草稿，不存在抛 404."""
    try:
        return DesignDraft.objects.get(pk=draft_id)
    except DesignDraft.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "草稿不存在") from None


def _draft_dict(draft: DesignDraft) -> dict[str, Any]:
    """构造草稿响应字典."""
    return {
        "id": draft.pk,
        "name": draft.name,
        "datasource_id": draft.datasource_id,  # type: ignore[missing-attribute]
        "table_name": draft.table_name,
        "schema_name": draft.schema_name or None,
        "spec": draft.spec,
        "status": draft.status,
        "created_at": draft.created_at.isoformat(),  # type: ignore[missing-attribute]
        "updated_at": draft.updated_at.isoformat(),  # type: ignore[missing-attribute]
    }


def _version_dict(version: DesignVersion) -> dict[str, Any]:
    """构造版本响应字典."""
    return {
        "id": version.pk,
        "draft_id": version.draft_id,  # type: ignore[missing-attribute]
        "version_no": version.version_no,
        "spec": version.spec,
        "created_at": version.created_at.isoformat(),  # type: ignore[missing-attribute]
    }


def _spec_to_dict(spec: TableDesignSpec) -> dict[str, Any]:
    """将 TableDesignSpec 转为可入库的 dict（嵌套字段也转为 dict）."""
    return {
        "name": spec.name,
        "schema_name": spec.schema_name,
        "comment": spec.comment,
        "fields": [f.model_dump() for f in spec.fields],
        "indexes": [i.model_dump() for i in spec.indexes],
        "foreign_keys": [fk.model_dump() for fk in spec.foreign_keys],
    }


def _dict_to_spec(spec_dict: dict[str, Any]) -> TableDesignSpec:
    """将 dict 转回 TableDesignSpec（嵌套字段也转换）."""
    return TableDesignSpec(
        name=spec_dict["name"],
        schema_name=spec_dict.get("schema_name"),
        comment=spec_dict.get("comment"),
        fields=[FieldSpec(**f) for f in spec_dict.get("fields", [])],
        indexes=[IndexSpec(**i) for i in spec_dict.get("indexes", [])],
        foreign_keys=[ForeignKeySpec(**fk) for fk in spec_dict.get("foreign_keys", [])],
    )


def _next_version_no(draft: DesignDraft) -> int:
    """计算草稿下一个版本号（基于已有最大版本号 + 1）."""
    latest = draft.versions.order_by("-version_no").first()  # type: ignore[missing-attribute]
    if latest is None:
        return 1
    return latest.version_no + 1  # type: ignore[no-any-return]


def _create_version(draft: DesignDraft, user: User | None) -> DesignVersion:
    """为草稿创建当前 spec 的版本快照."""
    version = DesignVersion(
        draft=draft,
        version_no=_next_version_no(draft),
        spec=draft.spec,
        created_by=user,
    )
    version.save()
    return version


# ----- 草稿 CRUD -----


@router.get("/drafts", response={200: list[DraftOut]})
def list_drafts_view(request: HttpRequest, datasource_id: int | None = None) -> HttpResponse:
    """获取草稿列表（designer+，可按数据源过滤）."""
    require_designer_or_admin(request)
    qs = DesignDraft.objects.all()
    if datasource_id is not None:
        qs = qs.filter(datasource_id=datasource_id)
    qs = qs.order_by("-id")
    body = [DraftOut(**_draft_dict(d)).model_dump() for d in qs]
    return JsonResponse(body, safe=False)


@router.post("/drafts", response={201: DraftOut})
def create_draft_view(request: HttpRequest, payload: DraftCreateIn) -> HttpResponse:
    """创建草稿（designer+），同时创建首个版本."""
    require_designer_or_admin(request)
    # 校验数据源存在
    _get_ds_or_404(payload.datasource_id)
    user = cast(User, getattr(request, "auth", None))
    schema_name = payload.schema_name or ""
    if DesignDraft.objects.filter(
        datasource_id=payload.datasource_id,
        table_name=payload.table_name,
        schema_name=schema_name,
    ).exists():
        raise HttpError(400, "该表已存在草稿")
    spec_dict = _spec_to_dict(payload.spec)
    draft = DesignDraft(
        name=payload.name,
        datasource_id=payload.datasource_id,
        table_name=payload.table_name,
        schema_name=schema_name,
        spec=spec_dict,
        created_by=user,
    )
    draft.save()
    _create_version(draft, user)
    log_audit(
        request,
        action=AuditAction.DRAFT_CREATE,
        resource_type="draft",
        resource_id=str(draft.pk),
        datasource_id=payload.datasource_id,
        extra={"name": payload.name, "table_name": payload.table_name},
    )
    body = DraftOut(**_draft_dict(draft)).model_dump()
    return JsonResponse(body, status=201)


@router.get("/drafts/{draft_id}", response={200: DraftOut})
def retrieve_draft_view(request: HttpRequest, draft_id: int) -> HttpResponse:
    """获取草稿详情（designer+）."""
    require_designer_or_admin(request)
    draft = _get_draft_or_404(draft_id)
    body = DraftOut(**_draft_dict(draft)).model_dump()
    return JsonResponse(body)


@router.patch("/drafts/{draft_id}", response={200: DraftOut})
def update_draft_view(request: HttpRequest, draft_id: int, payload: DraftUpdateIn) -> HttpResponse:
    """更新草稿（designer+），自动创建版本快照."""
    require_designer_or_admin(request)
    draft = _get_draft_or_404(draft_id)
    user = cast(User, getattr(request, "auth", None))
    data = payload.model_dump(exclude_unset=True)

    if "name" in data:
        draft.name = data["name"]  # type: ignore[bad-assignment]
    if "table_name" in data or "schema_name" in data:
        new_table_name = data.get("table_name", draft.table_name)
        new_schema_name = data.get("schema_name", draft.schema_name)
        # 唯一性校验（排除自身）
        if (
            DesignDraft.objects.filter(
                datasource_id=draft.datasource_id,  # type: ignore[missing-attribute]
                table_name=new_table_name,
                schema_name=new_schema_name,
            )
            .exclude(pk=draft.pk)
            .exists()
        ):
            raise HttpError(400, "该表已存在草稿")
        draft.table_name = new_table_name  # type: ignore[bad-assignment]
        draft.schema_name = new_schema_name  # type: ignore[bad-assignment]
    if "spec" in data and data["spec"] is not None:
        # payload.spec 是 TableDesignSpec 实例（ninja Schema 解析后）
        draft.spec = _spec_to_dict(payload.spec)  # type: ignore[bad-assignment]

    draft.save()
    _create_version(draft, user)
    log_audit(
        request,
        action=AuditAction.DRAFT_UPDATE,
        resource_type="draft",
        resource_id=str(draft.pk),
        datasource_id=draft.datasource_id,  # type: ignore[missing-attribute]
        extra={"name": draft.name, "table_name": draft.table_name},
    )
    body = DraftOut(**_draft_dict(draft)).model_dump()
    return JsonResponse(body)


@router.delete("/drafts/{draft_id}", response={200: MessageOut})
def delete_draft_view(request: HttpRequest, draft_id: int) -> HttpResponse:
    """删除草稿及其所有版本（designer+）."""
    require_designer_or_admin(request)
    draft = _get_draft_or_404(draft_id)
    draft_id_val = draft.pk
    ds_id = draft.datasource_id  # type: ignore[missing-attribute]
    draft_name = draft.name
    draft.delete()
    log_audit(
        request,
        action=AuditAction.DRAFT_DELETE,
        resource_type="draft",
        resource_id=str(draft_id_val),
        datasource_id=ds_id,
        extra={"name": draft_name},
    )
    return JsonResponse({"detail": "已删除"})


# ----- 版本管理 -----


@router.get("/drafts/{draft_id}/versions", response={200: list[VersionOut]})
def list_versions_view(request: HttpRequest, draft_id: int) -> HttpResponse:
    """获取草稿的版本列表（所有登录用户）."""
    del request  # 仅认证用
    draft = _get_draft_or_404(draft_id)
    versions = draft.versions.order_by("-version_no")  # type: ignore[missing-attribute]
    body = [VersionOut(**_version_dict(v)).model_dump() for v in versions]
    return JsonResponse(body, safe=False)


@router.post(
    "/drafts/{draft_id}/versions/{version_no}/rollback",
    response={200: DraftOut},
)
def rollback_to_version_view(
    request: HttpRequest,
    draft_id: int,
    version_no: int,
) -> HttpResponse:
    """回滚到指定版本（designer+）.

    把指定版本的 spec 作为新版本保存到草稿（草稿当前 spec 被覆盖）。
    原版本数据不删除，便于追溯。
    """
    require_designer_or_admin(request)
    draft = _get_draft_or_404(draft_id)
    user = cast(User, getattr(request, "auth", None))
    try:
        version = draft.versions.get(version_no=version_no)  # type: ignore[missing-attribute]
    except DesignVersion.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "版本不存在") from None
    draft.spec = version.spec  # type: ignore[bad-assignment]
    draft.save()
    _create_version(draft, user)
    log_audit(
        request,
        action=AuditAction.DRAFT_ROLLBACK,
        resource_type="draft",
        resource_id=str(draft.pk),
        datasource_id=draft.datasource_id,  # type: ignore[missing-attribute]
        extra={"version_no": version_no},
    )
    body = DraftOut(**_draft_dict(draft)).model_dump()
    return JsonResponse(body)


# ----- DDL 反射转 spec（用于自动判断 CREATE/ALTER）-----

# 反射类型字符串解析：如 "VARCHAR(50)" → ("VARCHAR", 50)；"DECIMAL(10, 2)" → ("DECIMAL", 10)
_REFLECTED_TYPE_RE = re.compile(r"^\s*(.+?)\s*(?:\(\s*(\d+)\s*(?:,\s*\d+\s*)?\))?\s*$")


def _parse_reflected_type(type_str: str) -> tuple[str, int | None]:
    """解析反射得到的类型字符串为基础类型与长度.

    反射返回的 ``ColumnMeta.type`` 为完整类型字符串（如 ``VARCHAR(50)``、``INTEGER``），
    需拆分为 ``FieldSpec.type`` 与 ``FieldSpec.length`` 以便与设计器 spec 对齐比较。
    含精度的类型（如 ``DECIMAL(10, 2)``）仅取首位作为长度，scale 不参与比较。
    """
    m = _REFLECTED_TYPE_RE.match(type_str)
    if not m:
        return type_str.strip().upper(), None
    base = m.group(1).strip().upper()
    length = int(m.group(2)) if m.group(2) else None
    return base, length


def _reflect_table_to_spec(meta: TableMeta, dialect: str) -> TableDesignSpec:
    """将反射的表元数据转为 TableDesignSpec，用作 ALTER 比较的 old_spec.

    规整反射与设计器约定间"语义等价但表示不同"的差异，避免误判触发无意义 ALTER：
    - 主键列强制 ``nullable=False``（主键必非空，部分方言反射返回 True）。
    - SQLite 的 ``INTEGER PRIMARY KEY`` 隐式 ROWID 自增，对齐 ``autoincrement=True``。
    - 自增主键的 default 规整为 None（序列由数据库隐含管理，反射得到的序列表达式
      与设计器 None 不一致会误判为 default 变更）。
    外键 on_delete 反射无法获取，统一填 RESTRICT（外键差异仅按 name 比较，不影响）。
    """
    fields: list[FieldSpec] = []
    for col in meta.columns:
        base_type, length = _parse_reflected_type(col.type)
        # 主键列必非空
        nullable = False if col.primary_key else col.nullable
        autoincrement = col.autoincrement
        if dialect == EngineType.SQLITE and col.primary_key and base_type in ("INTEGER", "INT"):
            autoincrement = True
        default = col.default
        if autoincrement and col.primary_key:
            default = None
        fields.append(
            FieldSpec(
                name=col.name,
                type=base_type,
                length=length,
                nullable=nullable,
                default=default,
                comment=col.comment,
                primary_key=col.primary_key,
                unique=col.unique,
                autoincrement=autoincrement,
            )
        )
    indexes = [IndexSpec(name=i.name, columns=list(i.columns), unique=i.unique) for i in meta.indexes]
    foreign_keys = [
        ForeignKeySpec(
            name=fk.name,
            columns=list(fk.columns),
            referred_table=fk.referred_table,
            referred_columns=list(fk.referred_columns),
            on_delete="RESTRICT",
        )
        for fk in meta.foreign_keys
    ]
    return TableDesignSpec(
        name=meta.name,
        schema_name=meta.schema,
        comment=meta.comment,
        fields=fields,
        indexes=indexes,
        foreign_keys=foreign_keys,
    )


def _resolve_old_spec(ds: DataSource, spec: TableDesignSpec) -> TableDesignSpec | None:
    """根据目标表是否存在自动构造 old_spec.

    表不存在时返回 None（走 CREATE）；存在时反射当前表结构转为 spec（走 ALTER）。
    SQLite 无 schema 概念，强制 schema=None。
    """
    engine = get_engine(ds)
    schema = spec.schema_name or None
    dialect = cast(str, ds.engine)
    if dialect == EngineType.SQLITE:
        schema = None
    if not sa_inspect(engine).has_table(spec.name, schema=schema):
        return None
    try:
        meta = inspect_table(engine, spec.name, schema=schema)
    except NoSuchTableError:
        return None
    return _reflect_table_to_spec(meta, dialect)


# ----- DDL 预览与执行 -----


@router.post("/ddl/preview", response={200: DDLPreviewOut})
def preview_ddl_view(request: HttpRequest, payload: DDLPreviewIn) -> HttpResponse:
    """预览 DDL 语句（所有登录用户）.

    传入 ``old_spec`` 时按显式 ALTER 生成；未传时自动判断：
    目标表已存在则反射其结构作为 old_spec 生成 ALTER，否则生成 CREATE。
    """
    del request  # 仅认证用
    ds = _get_ds_or_404(payload.datasource_id)
    old_spec = payload.old_spec
    if old_spec is None:
        old_spec = _resolve_old_spec(ds, payload.spec)
    try:
        result = generate_ddl(payload.spec, cast(str, ds.engine), old_spec=old_spec)
    except DDLError as exc:
        raise HttpError(400, str(exc)) from None
    return JsonResponse({"statements": list(result.statements)})


@router.post("/drafts/{draft_id}/apply", response={200: DDLExecuteOut})
def apply_draft_view(
    request: HttpRequest,
    draft_id: int,
    payload: DDLExecuteIn,
) -> HttpResponse:
    """应用草稿：执行 DDL 到目标数据源（designer+）.

    传入 ``old_spec`` 时按显式 ALTER 执行；未传时自动判断：
    目标表已存在则反射其结构作为 old_spec 执行 ALTER，否则执行 CREATE。
    执行成功后草稿状态置为 applied。
    """
    require_designer_or_admin(request)
    draft = _get_draft_or_404(draft_id)
    ds = draft.datasource
    spec = _dict_to_spec(cast("dict[str, Any]", draft.spec))
    old_spec = payload.old_spec
    if old_spec is None:
        old_spec = _resolve_old_spec(ds, spec)
    try:
        result = generate_ddl(spec, cast(str, ds.engine), old_spec=old_spec)
    except DDLError as exc:
        raise HttpError(400, str(exc)) from None
    if not result.statements:
        return JsonResponse({"executed": 0, "statements": []})
    engine = get_engine(ds)
    try:
        with engine.begin() as conn:
            for stmt in result.statements:
                conn.execute(text(stmt))
    except SQLAlchemyError as exc:
        log_audit(
            request,
            action=AuditAction.DDL_APPLY,
            status=AuditStatus.FAILURE,
            resource_type="draft",
            resource_id=str(draft.pk),
            datasource_id=ds.pk,
            datasource_name=ds.name,
            sql="\n".join(result.statements),
            error_message=f"DDL 执行失败: {exc}",
        )
        raise HttpError(400, f"DDL 执行失败: {exc}") from None
    # 执行成功后更新草稿状态
    draft.status = DraftStatus.APPLIED  # type: ignore[bad-assignment]
    draft.save()
    log_audit(
        request,
        action=AuditAction.DDL_APPLY,
        resource_type="draft",
        resource_id=str(draft.pk),
        datasource_id=ds.pk,
        datasource_name=ds.name,
        sql="\n".join(result.statements),
        row_count=len(result.statements),
        extra={"statements": list(result.statements)},
    )
    body = DDLExecuteOut(
        executed=len(result.statements),
        statements=list(result.statements),
    ).model_dump()
    return JsonResponse(body)
