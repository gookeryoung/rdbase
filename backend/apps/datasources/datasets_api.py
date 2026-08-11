"""数据集（Dataset）Router：对外稳定契约 + 管理面 CRUD.

设计要点：

- **管理端点**（``GET/POST/PATCH/DELETE``、``GET /{slug}/preview``）：``JWTAuth`` 认证 +
  ``require_admin`` 校验，复用 ``log_audit`` 记录 ``DATASET_*`` 审计动作。
- **公开查询端点**（``GET /{slug}/rows``）：``ApiTokenAuth`` 认证 + ``datasets:read``
  scope 校验，供外部应用按 slug 查询数据。
- **公开写入端点**（``POST /{slug}/rows``）：``ApiTokenAuth`` 认证 +
  ``datasets:write`` scope 校验，单行/批量 UPSERT；接入限流、每日配额、幂等保护
  与 ``DATASET_WRITE`` 审计。
- **行级过滤**：``Dataset.filter_expression`` 与查询时 ``filters`` 参数 AND 组合；
  同名列以 Dataset 配置为准，防止调用方绕过。
- **列级权限**：``Dataset.fields_whitelist`` 非空时，请求 ``columns`` 必须是其子集，
  否则 400；``columns`` 未指定时返回白名单列（或全部列）。写入端点同样校验
  ``fields_whitelist``：rows 的所有键必须是白名单子集，实现列级写权限。
- **is_active=False 不可查询/写入**：公开端点对未启用数据集返回 404；管理端点仍可访问。
- **version 自增**：每次 PATCH 自动 ``increment_version``，调用方可据此检测契约变化。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import quote

from django.conf import settings
from django.db import connections
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse, StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError
from sqlalchemy.exc import SQLAlchemyError

from apps.accounts.auth import ApiTokenAuth, JWTAuth
from apps.accounts.models import ApiToken, User
from apps.accounts.permissions import require_admin
from apps.audit.audit import log_audit
from apps.audit.models import AuditAction, AuditStatus
from apps.ingest.models import ConflictStrategy
from apps.ingest.writer import write_rows
from apps.manager.query import (
    QueryError,
    get_column_names,
    get_pk_columns,
    iter_filtered_table_rows,
    query_table_rows,
    rows_to_csv,
)
from apps.sync.models import SyncConfig
from apps.sync.sync_service import SyncError, SyncService
from apps.system.distributed_lock import get_lock
from apps.system.idempotency import (
    check_idempotency,
    release_idempotency,
    store_idempotency_result,
)
from apps.system.quota import check_and_consume_quota
from apps.system.rate_limiter import check_rate_limit, check_token_bucket

from .engine import get_engine
from .models import Dataset, DataSource
from .schemas import (
    DatasetCreateIn,
    DatasetListOut,
    DatasetOut,
    DatasetRowsOut,
    DatasetSyncTriggerOut,
    DatasetUpdateIn,
    DatasetWriteIn,
    DatasetWriteOut,
    MessageOut,
)

# 单批写入行数上限（防一次性写入过大请求体）。
_MAX_BATCH_ROWS = 1000

# 管理端 Router：默认 JWT 认证，公开端点通过路由级 auth 覆盖
router = Router(tags=["datasets"], auth=JWTAuth())


# ============================================================
# 辅助函数
# ============================================================


def _dataset_to_dict(ds: Dataset) -> dict[str, Any]:
    """构造 Dataset 响应字典."""
    return {
        "id": ds.pk,
        "slug": ds.slug,
        "name": ds.name,
        "description": ds.description,
        "datasource_id": ds.datasource_id,
        "table_name": ds.table_name,
        "schema_name": ds.schema_name,
        "fields_whitelist": list(cast("list[str]", ds.fields_whitelist)),
        "filter_expression": dict(cast("dict[str, Any]", ds.filter_expression)),
        "aggregations": dict(cast("dict[str, Any]", ds.aggregations)),
        "owner_id": ds.owner_id,
        "sync_config_id": ds.sync_config_id,
        "is_active": ds.is_active,
        "version": ds.version,
        "created_at": ds.created_at.isoformat(),  # type: ignore[missing-attribute]
        "updated_at": ds.updated_at.isoformat(),  # type: ignore[missing-attribute]
    }


def _get_dataset_or_404(slug: str, *, active_only: bool = False) -> Dataset:
    """按 slug 获取数据集，不存在抛 404.

    Args:
        slug: 数据集 slug。
        active_only: True 时 ``is_active=False`` 也抛 404（用于公开查询端点）。
    """
    try:
        ds = Dataset.objects.get(slug=slug)
    except Dataset.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"数据集 {slug} 不存在") from None
    if active_only and not ds.is_active:
        raise HttpError(404, f"数据集 {slug} 不存在")
    return ds


def _require_scope(request: HttpRequest, scope: str) -> ApiToken:
    """校验请求携带 ApiToken 且拥有指定 scope，否则抛 403.

    ApiTokenAuth 已校验 Token 有效性并挂载到 ``request.api_token``；此处仅校验 scope。
    无 ApiToken（如 JWT 访问公开端点）也拒绝，强制走 Token 路径。
    """
    token = getattr(request, "api_token", None)
    if not isinstance(token, ApiToken):
        raise HttpError(403, "此端点须使用 API Token 访问")
    if not token.has_scope(scope):
        raise HttpError(403, f"Token 缺少 scope: {scope}")
    return token


def _normalize_filter_expr(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """规范化 filter_expression 为 ``{列名: {"op":..., "val":...}}`` 结构.

    简写形式 ``{"col": val}`` 转换为 ``{"col": {"op": "eq", "val": val}}``；
    已是 ``{"op","val"}`` 结构的原样保留；非 dict 值视为简写 eq。

    Args:
        raw: 原始 filter_expression dict。

    Returns:
        规范化后的 filters dict，与 ``query_table_rows`` 入参格式一致。
    """
    normalized: dict[str, dict[str, Any]] = {}
    for col, cond in raw.items():
        if isinstance(cond, dict) and "op" in cond and "val" in cond:
            normalized[col] = {"op": cond["op"], "val": cond["val"]}
        else:
            # 简写：值即 eq 比较的右值
            normalized[col] = {"op": "eq", "val": cond}
    return normalized


def _merge_filters(
    dataset_filters: dict[str, dict[str, Any]],
    user_filters: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """合并 Dataset 行级过滤与用户 filters，AND 组合.

    同名列以 dataset_filters 为准（防绕过）；不同列合并保留。
    """
    merged = dict(dataset_filters)
    for col, cond in user_filters.items():
        if col not in merged:
            merged[col] = cond
    return merged


def _resolve_columns(
    whitelist: list[str],
    user_columns: list[str] | None,
) -> list[str] | None:
    """根据白名单与用户请求列计算实际 SELECT 列.

    Args:
        whitelist: Dataset.fields_whitelist；空列表表示允许全部列。
        user_columns: 用户请求的 columns；None 表示未指定。

    Returns:
        实际查询的列名列表；None 表示查询全部列。

    Raises:
        HttpError: 白名单非空且 user_columns 非其子集时 400。
    """
    if not whitelist:
        # 无白名单：原样返回用户请求（None 或具体列表）
        return user_columns
    if user_columns is None:
        # 有白名单但用户未指定：返回白名单
        return list(whitelist)
    # 用户请求列必须是白名单子集
    whitelist_set = set(whitelist)
    invalid = [c for c in user_columns if c not in whitelist_set]
    if invalid:
        raise HttpError(400, f"请求列不在白名单内: {invalid}")
    return user_columns


def _parse_filters_param(filters_param: str | None) -> dict[str, dict[str, Any]]:
    """解析 filters JSON 字符串参数为 dict（与 manager.api._parse_filters 同语义）."""
    if not filters_param:
        return {}
    try:
        parsed = json.loads(filters_param)
    except json.JSONDecodeError as exc:
        raise HttpError(400, f"filters 参数非法 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise HttpError(400, "filters 须为 JSON 对象")
    return cast("dict[str, dict[str, Any]]", parsed)


def _parse_columns_param(columns_param: str | None) -> list[str] | None:
    """解析 columns 逗号分隔字符串为列表（None 表示查询所有列）."""
    if not columns_param:
        return None
    cols = [c.strip() for c in columns_param.split(",") if c.strip()]
    return cols or None


def _query_dataset_rows(  # noqa: PLR0913
    dataset: Dataset,
    *,
    page: int,
    page_size: int,
    order_by: str | None,
    order_dir: str,
    columns_param: str | None,
    filters_param: str | None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """执行数据集行查询的通用逻辑（供公开 /rows 与管理 /preview 复用）.

    返回 ``(rows, total, returned_columns)``。

    流程：

    1. 解析并规范化 filters（用户 filters + Dataset.filter_expression 合并，同名列以
       Dataset 为准防绕过）。
    2. 解析 columns（白名单裁剪：用户列必须是白名单子集，否则 400）。
    3. 调用 ``query_table_rows`` 执行查询。
    4. 计算 ``returned_columns``：显式列 → 反射列 → 空列表。
    """
    ds: DataSource = dataset.datasource
    if not ds.is_active:
        raise HttpError(404, "数据源已停用")

    user_filters = _parse_filters_param(filters_param)
    user_columns = _parse_columns_param(columns_param)

    dataset_filters = _normalize_filter_expr(dict(cast("dict[str, Any]", dataset.filter_expression)))
    merged_filters = _merge_filters(dataset_filters, user_filters)

    selected_columns = _resolve_columns(list(cast("list[str]", dataset.fields_whitelist)), user_columns)

    engine = get_engine(ds)
    schema = dataset.schema_name or None
    try:
        rows, total = query_table_rows(
            engine,
            table_name=dataset.table_name,
            schema=schema,
            columns=selected_columns,
            page=page,
            page_size=page_size,
            order_by=order_by,
            order_dir=order_dir,
            filters=merged_filters,
        )
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"查询失败: {exc}") from None

    returned_columns: list[str]
    if selected_columns:
        returned_columns = selected_columns
    elif rows:
        returned_columns = list(rows[0].keys())
    else:
        try:
            returned_columns = get_column_names(engine, dataset.table_name, schema)
        except QueryError:
            returned_columns = []

    return rows, total, returned_columns


# ============================================================
# 管理端点：CRUD
# ============================================================


@router.get("", response={200: DatasetListOut})
def list_datasets(request: HttpRequest) -> HttpResponse:
    """列出全部数据集（仅管理员）."""
    require_admin(request)
    qs = Dataset.objects.all().order_by("-id")
    items = [_dataset_to_dict(d) for d in qs]
    body = DatasetListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.post("", response={201: DatasetOut})
def create_dataset(request: HttpRequest, payload: DatasetCreateIn) -> HttpResponse:
    """创建数据集（仅管理员）.

    slug 唯一；datasource_id 必须指向已存在的数据源。
    """
    require_admin(request)
    if Dataset.objects.filter(slug=payload.slug).exists():
        raise HttpError(400, "slug 已存在")
    if not DataSource.objects.filter(pk=payload.datasource_id).exists():
        raise HttpError(400, "数据源不存在")
    if payload.sync_config_id is not None and not SyncConfig.objects.filter(pk=payload.sync_config_id).exists():
        raise HttpError(400, "同步配置不存在")
    user = cast(User, getattr(request, "auth", None))
    ds = Dataset(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        datasource_id=payload.datasource_id,
        table_name=payload.table_name,
        schema_name=payload.schema_name,
        fields_whitelist=list(payload.fields_whitelist),
        filter_expression=dict(payload.filter_expression),
        aggregations=dict(payload.aggregations),
        owner=user,
        sync_config_id=payload.sync_config_id,
        is_active=payload.is_active,
    )
    ds.save()
    log_audit(
        request,
        action=AuditAction.DATASET_CREATE,
        resource_type="dataset",
        resource_id=str(ds.pk),
        extra={"slug": ds.slug, "name": ds.name, "datasource_id": ds.datasource_id},
    )
    body = _dataset_to_dict(ds)
    return JsonResponse(body, status=201)


@router.get("/{slug}", response={200: DatasetOut})
def retrieve_dataset(request: HttpRequest, slug: str) -> HttpResponse:
    """获取数据集详情（仅管理员；含 is_active=False 的）."""
    require_admin(request)
    ds = _get_dataset_or_404(slug)
    return JsonResponse(_dataset_to_dict(ds))


@router.patch("/{slug}", response={200: DatasetOut})
def update_dataset(request: HttpRequest, slug: str, payload: DatasetUpdateIn) -> HttpResponse:
    """更新数据集（仅管理员）.

    slug 修改后重名返回 400；datasource_id 修改后须指向已存在数据源；
    每次更新 version 自增。
    """
    require_admin(request)
    ds = _get_dataset_or_404(slug)
    data = payload.model_dump(exclude_unset=True)

    if "slug" in data and data["slug"] != ds.slug and Dataset.objects.filter(slug=data["slug"]).exists():
        raise HttpError(400, "slug 已存在")
    if "datasource_id" in data and not DataSource.objects.filter(pk=data["datasource_id"]).exists():
        raise HttpError(400, "数据源不存在")
    if (
        "sync_config_id" in data
        and data["sync_config_id"] is not None
        and not SyncConfig.objects.filter(pk=data["sync_config_id"]).exists()
    ):
        raise HttpError(400, "同步配置不存在")

    # 白名单类字段强制复制为 list/dict，避免共享引用
    if "fields_whitelist" in data and data["fields_whitelist"] is not None:
        data["fields_whitelist"] = list(data["fields_whitelist"])
    if "filter_expression" in data and data["filter_expression"] is not None:
        data["filter_expression"] = dict(data["filter_expression"])
    if "aggregations" in data and data["aggregations"] is not None:
        data["aggregations"] = dict(data["aggregations"])

    for field, value in data.items():
        setattr(ds, field, value)
    ds.increment_version()
    ds.save()
    log_audit(
        request,
        action=AuditAction.DATASET_UPDATE,
        resource_type="dataset",
        resource_id=str(ds.pk),
        extra={"slug": ds.slug, "version": ds.version},
    )
    return JsonResponse(_dataset_to_dict(ds))


@router.delete("/{slug}", response={200: MessageOut})
def delete_dataset(request: HttpRequest, slug: str) -> HttpResponse:
    """删除数据集（仅管理员）."""
    require_admin(request)
    ds = _get_dataset_or_404(slug)
    ds_id = ds.pk
    ds_slug = ds.slug
    ds.delete()
    log_audit(
        request,
        action=AuditAction.DATASET_DELETE,
        resource_type="dataset",
        resource_id=str(ds_id),
        extra={"slug": ds_slug},
    )
    return JsonResponse(MessageOut(detail=f"数据集 {ds_slug} 已删除").model_dump())


# ============================================================
# 管理端点：预览
# ============================================================


@router.get("/{slug}/preview", response={200: DatasetRowsOut})
def preview_rows(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    slug: str,
    page: int = 1,
    page_size: int = 20,
    order_by: str | None = None,
    order_dir: str = "asc",
    columns: str | None = None,
    filters: str | None = None,
) -> HttpResponse:
    """管理员预览数据集行（不走 scope 校验；用于诊断/调试）.

    与公开 ``/{slug}/rows`` 的差异：使用 JWTAuth + require_admin，不做 scope 校验；
    允许预览 ``is_active=False`` 的数据集（仍校验数据源 is_active）。
    """
    require_admin(request)
    del request  # 已用 require_admin 校验
    ds = _get_dataset_or_404(slug)
    rows, total, returned_columns = _query_dataset_rows(
        ds,
        page=page,
        page_size=page_size,
        order_by=order_by,
        order_dir=order_dir,
        columns_param=columns,
        filters_param=filters,
    )
    body = DatasetRowsOut(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        columns=returned_columns,
    ).model_dump()
    return JsonResponse(body)


# ============================================================
# 管理端点：导出 CSV
# ============================================================


@router.get("/{slug}/export")
def export_dataset_rows(
    request: HttpRequest,
    slug: str,
    format: str = "csv",
    columns: str | None = None,
    filters: str | None = None,
) -> HttpResponseBase:
    """导出数据集行为 CSV（所有登录用户可读，JWT 认证）.

    Query 参数：

        format: 导出格式，仅 ``csv``（默认）。
        columns: 逗号分隔的列名列表；为空时按白名单或全部列返回。
        filters: JSON 字符串，格式 ``{"列名":{"op":"eq/ne/...","val":...}}``，
                 与 Dataset.filter_expression AND 组合，同名列以 Dataset 为准。

    行为：

    - 复用 ``_query_dataset_rows`` 的列裁剪与 filter 合并逻辑（白名单子集校验、
      Dataset.filter_expression 强制行级过滤）。
    - ``is_active=False`` 的数据集返回 404；数据源 ``is_active=False`` 返回 404。
    - 限流：按 ``dataset_export:{user_id}`` 维度令牌桶限流，超限 429 + ``Retry-After``。
    - 流式响应：``StreamingHttpResponse`` + ``rows_to_csv``，UTF-8 BOM，
      ``Content-Disposition: attachment; filename="{slug}.csv"``。

    审计与权限过滤增强在 P9-Q2 实施；当前仅基础导出 + 限流。
    """
    fmt = format.lower()
    if fmt != "csv":
        raise HttpError(400, f"不支持的导出格式: {format}（数据集导出仅支持 csv）")

    user = cast(User, getattr(request, "auth", None))
    if user is None or not isinstance(user, User):  # pragma: no cover - JWTAuth 已校验
        raise HttpError(401, "未认证")

    # 令牌桶限流：按 user_id 维度，防止滥用导出大表
    rate_key = f"dataset_export:{user.pk}"
    allowed, retry_after = check_token_bucket(
        rate_key,
        capacity=settings.RATE_LIMIT_DATASET_EXPORT_CAPACITY,
        refill_rate=settings.RATE_LIMIT_DATASET_EXPORT_REFILL_RATE,
    )
    if not allowed:
        resp = JsonResponse(
            {"detail": f"导出请求过于频繁，请 {retry_after} 秒后重试"},
            status=429,
        )
        resp["Retry-After"] = str(retry_after)
        return resp

    ds = _get_dataset_or_404(slug, active_only=True)
    datasource: DataSource = ds.datasource
    if not datasource.is_active:
        raise HttpError(404, "数据源已停用")

    # 解析并合并 filters / columns（复用 /rows 的逻辑）
    user_filters = _parse_filters_param(filters)
    user_columns = _parse_columns_param(columns)
    dataset_filters = _normalize_filter_expr(dict(cast("dict[str, Any]", ds.filter_expression)))
    merged_filters = _merge_filters(dataset_filters, user_filters)
    selected_columns = _resolve_columns(
        list(cast("list[str]", ds.fields_whitelist)),
        user_columns,
    )

    engine = get_engine(datasource)
    schema = ds.schema_name or None

    # Eager 校验表存在并获取列名（避免生成器延迟到流式阶段才抛错，此时响应头已发送）
    try:
        table_columns = get_column_names(engine, ds.table_name, schema)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"读取表元数据失败: {exc}") from None

    # 校验用户请求列在表内（selected_columns 已过白名单校验，但需确认列确实存在于表）
    if selected_columns:
        table_cols_set = set(table_columns)
        invalid = [c for c in selected_columns if c not in table_cols_set]
        if invalid:
            raise HttpError(400, f"非法列名: {invalid}")
        returned_columns = selected_columns
    else:
        returned_columns = table_columns

    try:
        rows_iter = iter_filtered_table_rows(
            engine,
            table_name=ds.table_name,
            schema=schema,
            columns=selected_columns,
            filters=merged_filters,
        )
        chunks: Iterator[str] = rows_to_csv(rows_iter, returned_columns)
    except QueryError as exc:  # pragma: no cover - eager 校验已拦截大部分场景
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:  # pragma: no cover
        raise HttpError(400, f"导出失败: {exc}") from None

    filename = f"{slug}.csv"
    disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"

    def _stream() -> Iterator[bytes]:
        try:
            for chunk in chunks:
                yield chunk.encode("utf-8")
        except QueryError as exc:  # pragma: no cover - eager 校验已拦截
            yield f"\n[导出错误] {exc}".encode()
        except SQLAlchemyError as exc:  # pragma: no cover
            yield f"\n[导出错误] {exc}".encode()

    resp = StreamingHttpResponse(_stream(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = disposition
    return resp


# ============================================================
# 公开查询端点：/rows
# ============================================================


@router.get("/{slug}/rows", response={200: DatasetRowsOut}, auth=ApiTokenAuth())
def query_rows(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    slug: str,
    page: int = 1,
    page_size: int = 20,
    order_by: str | None = None,
    order_dir: str = "asc",
    columns: str | None = None,
    filters: str | None = None,
) -> HttpResponse:
    """外部应用按 slug 查询数据集行（API Token + datasets:read scope）.

    Query 参数：

        page: 页码，从 1 开始，默认 1。
        page_size: 每页行数，默认 20。
        order_by: 排序字段（须为表内列名）。
        order_dir: 排序方向，``asc`` 或 ``desc``，默认 ``asc``。
        columns: 逗号分隔的列名列表；为空时按白名单或全部列返回。
        filters: JSON 字符串，格式 ``{"列名":{"op":"eq/ne/...","val":...}}``。
                 与 Dataset.filter_expression AND 组合，同名列以 Dataset 为准。

    行为：

    - ``is_active=False`` 的数据集返回 404；
    - 数据源 ``is_active=False`` 返回 404；
    - 字段白名单非空时，请求列必须是其子集，否则 400；
    - ``filter_expression`` 强制行级过滤，外部无法绕过。
    """
    _require_scope(request, "datasets:read")
    del request  # 已用 _require_scope 校验
    ds = _get_dataset_or_404(slug, active_only=True)
    rows, total, returned_columns = _query_dataset_rows(
        ds,
        page=page,
        page_size=page_size,
        order_by=order_by,
        order_dir=order_dir,
        columns_param=columns,
        filters_param=filters,
    )
    body = DatasetRowsOut(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        columns=returned_columns,
    ).model_dump()
    return JsonResponse(body)


# ============================================================
# 公开写入端点：POST /rows
# ============================================================


def _validate_conflict_strategy(strategy: str) -> str:
    """校验冲突策略取值合法，返回归一化后的策略值，非法抛 400."""
    valid = {choice.value for choice in ConflictStrategy}
    if strategy not in valid:
        raise HttpError(400, f"非法的冲突策略：{strategy}（可选：{', '.join(sorted(valid))}）")
    return strategy


def _collect_row_fields(rows: list[dict[str, Any]]) -> list[str]:
    """收集 rows 中出现过的全部字段名（保持首次出现顺序）."""
    seen: dict[str, None] = {}
    for row in rows:
        for col in row:
            seen.setdefault(col, None)
    return list(seen)


@router.post("/{slug}/rows", response={200: DatasetWriteOut}, auth=ApiTokenAuth())
def write_dataset_rows(  # noqa: PLR0912
    request: HttpRequest,
    slug: str,
    payload: DatasetWriteIn,
) -> HttpResponse:
    """外部应用按 slug 写入数据集行（API Token + datasets:write scope）.

    支持单行/批量 UPSERT，冲突策略复用 ``ConflictStrategy``（upsert/skip/error）。

    处理流程：

    1. ``datasets:write`` scope 校验。
    2. 幂等检查（``Idempotency-Key`` 命中则回放缓存）。
    3. 速率限制（每 Token 每分钟 ``RATE_LIMIT_DATASET_WRITE`` 次，超限 429）。
    4. 数据集存在性 + ``is_active`` 校验（404）；数据源 ``is_active`` 校验（404）。
    5. 入参校验：rows 非空（400）、单批 <= 1000 行（400）、冲突策略合法（400）。
    6. 列级写权限：``fields_whitelist`` 非空时 rows 所有键必须是其子集（400）；
       反射校验 rows 键是表列子集（400）。
    7. 主键推断：``pk_fields`` 未传时由 ``get_pk_columns`` 反射；无主键且策略非 error
       时 400（UPSERT/SKIP 依赖主键判定冲突）。
    8. 每日配额（``DATASET_WRITE_DAILY_QUOTA``，超限 429）。
    9. 调 ``write_rows`` 写入；记录 ``DATASET_WRITE`` 审计日志。
    10. 存幂等结果返回。
    """
    token = _require_scope(request, "datasets:write")

    # 幂等检查：命中已完成缓存则直接回放，命中 in_progress 返回 409。
    cached = check_idempotency(request)
    if cached is not None:
        return cached

    # 速率限制：每 Token 每分钟写入请求数上限。
    rate_key = f"dataset_write:{token.prefix}"
    allowed, retry_after = check_rate_limit(
        rate_key,
        max_requests=settings.RATE_LIMIT_DATASET_WRITE,
        window_seconds=60,
    )
    if not allowed:
        release_idempotency(request)
        resp = JsonResponse(
            {"detail": f"写入请求过于频繁，请 {retry_after} 秒后重试"},
            status=429,
        )
        resp["Retry-After"] = str(retry_after)
        return resp

    ds = _get_dataset_or_404(slug, active_only=True)
    if not ds.datasource.is_active:
        release_idempotency(request)
        raise HttpError(404, "数据源已停用")

    # 入参校验
    rows = payload.rows
    if not rows:
        release_idempotency(request)
        raise HttpError(400, "rows 不能为空")
    if len(rows) > _MAX_BATCH_ROWS:
        release_idempotency(request)
        raise HttpError(400, f"单批写入行数不能超过 {_MAX_BATCH_ROWS}")
    strategy = _validate_conflict_strategy(payload.conflict_strategy)

    engine = get_engine(ds.datasource)
    schema = ds.schema_name or None
    try:
        table_columns = get_column_names(engine, ds.table_name, schema)
    except QueryError as exc:
        release_idempotency(request)
        raise HttpError(400, str(exc)) from None

    table_cols_set = set(table_columns)
    # 列级写权限：白名单非空时 rows 键必须是其子集
    whitelist = list(cast("list[str]", ds.fields_whitelist))
    if whitelist:
        whitelist_set = set(whitelist)
        for i, row in enumerate(rows):
            invalid = [c for c in row if c not in whitelist_set]
            if invalid:
                release_idempotency(request)
                raise HttpError(400, f"第 {i} 行包含非白名单列: {invalid}")
    # 反射校验：rows 键必须是表列子集
    for i, row in enumerate(rows):
        unknown = [c for c in row if c not in table_cols_set]
        if unknown:
            release_idempotency(request)
            raise HttpError(400, f"第 {i} 行包含不存在的列: {unknown}")

    # 主键推断
    if payload.pk_fields:
        pk_fields = list(payload.pk_fields)
        # 校验传入的主键列确实存在
        bad_pk = [c for c in pk_fields if c not in table_cols_set]
        if bad_pk:
            release_idempotency(request)
            raise HttpError(400, f"pk_fields 包含不存在的列: {bad_pk}")
    else:
        try:
            pk_fields = get_pk_columns(engine, ds.table_name, schema)
        except QueryError as exc:
            release_idempotency(request)
            raise HttpError(400, str(exc)) from None
        if not pk_fields and strategy != ConflictStrategy.ERROR.value:
            release_idempotency(request)
            raise HttpError(
                400,
                "表无主键且冲突策略非 error，无法判定冲突；请显式传入 pk_fields 或改用 error 策略",
            )

    # 每日配额：每 Token 每日写入总行数上限
    quota_key = f"dataset_write_daily:{token.prefix}"
    quota_allowed, _remaining = check_and_consume_quota(
        quota_key,
        rows=len(rows),
        daily_limit=settings.DATASET_WRITE_DAILY_QUOTA,
    )
    if not quota_allowed:
        release_idempotency(request)
        resp = JsonResponse(
            {"detail": "已达每日写入配额上限，请明日再试"},
            status=429,
        )
        return resp

    # 写入目标表
    target_fields = _collect_row_fields(rows)
    start = time.perf_counter()
    try:
        written, skipped = write_rows(
            engine,
            rows,
            target_table=ds.table_name,
            target_fields=target_fields,
            pk_fields=pk_fields,
            conflict_strategy=strategy,
        )
    except ValueError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_audit(
            request,
            action=AuditAction.DATASET_WRITE,
            status=AuditStatus.FAILURE,
            resource_type="dataset",
            resource_id=str(ds.pk),
            datasource_id=ds.datasource_id,
            datasource_name=ds.datasource.name,
            row_count=len(rows),
            elapsed_ms=elapsed_ms,
            error_message=str(exc),
            extra={"slug": ds.slug, "strategy": strategy},
        )
        release_idempotency(request)
        raise HttpError(400, str(exc)) from exc

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log_audit(
        request,
        action=AuditAction.DATASET_WRITE,
        resource_type="dataset",
        resource_id=str(ds.pk),
        datasource_id=ds.datasource_id,
        datasource_name=ds.datasource.name,
        row_count=written + skipped,
        elapsed_ms=elapsed_ms,
        extra={
            "slug": ds.slug,
            "strategy": strategy,
            "written": written,
            "skipped": skipped,
        },
    )

    body = DatasetWriteOut(
        written=written,
        skipped=skipped,
        total=written + skipped,
    ).model_dump()
    store_idempotency_result(request, 200, body)
    return JsonResponse(body)


# ============================================================
# 公开触发端点：POST /{slug}/sync
# ============================================================


@router.post("/{slug}/sync", response={202: DatasetSyncTriggerOut}, auth=ApiTokenAuth())
def trigger_dataset_sync(request: HttpRequest, slug: str) -> HttpResponse:
    """外部应用按 slug 触发绑定的同步配置执行（API Token + sync:trigger scope）.

    接入幂等保护与分布式锁，**异步**在后台线程执行 ``SyncService.run`` 并立即返回
    ``task_id``，供调用方对账（执行结果以 ``SyncLog`` 为准）。

    处理流程：

    1. ``sync:trigger`` scope 校验。
    2. 数据集存在性 + ``is_active`` 校验（404）。
    3. 数据集须绑定 ``sync_config``（400）；同步配置须存在且 ``is_active``（400）。
    4. 幂等检查（``Idempotency-Key`` 命中回放缓存的 task_id）。
    5. 令牌桶限流 ``trigger:{token_prefix}``（超额返回 429 + ``Retry-After``）。
    6. 分布式锁 ``sync:config:{sync_config_id}``（占用返回 409）。
    7. 启动后台线程执行 ``SyncService(config).run()``，finally 释放锁与 DB 连接。
    8. 写 ``SYNC_TRIGGER`` 审计，存幂等结果返回 202 + ``task_id``。
    """
    _require_scope(request, "sync:trigger")

    ds = _get_dataset_or_404(slug, active_only=True)
    sync_config_id = ds.sync_config_id
    if sync_config_id is None:
        raise HttpError(400, "数据集未绑定同步配置")

    try:
        config = SyncConfig.objects.get(pk=sync_config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(400, "绑定的同步配置不存在") from None
    if not config.is_active:
        raise HttpError(400, "同步配置已暂停，请先启用")

    # 幂等检查：命中已完成缓存则回放 task_id，命中 in_progress 返回 409。
    cached = check_idempotency(request)
    if cached is not None:
        return cached

    # 令牌桶限流：按 Token 维度限流触发端点（sync trigger + ingest trigger 共享一个桶）。
    token = cast(ApiToken, getattr(request, "api_token", None))
    rate_key = f"trigger:{token.prefix}"
    allowed, retry_after = check_token_bucket(
        rate_key,
        capacity=settings.RATE_LIMIT_TRIGGER_CAPACITY,
        refill_rate=settings.RATE_LIMIT_TRIGGER_REFILL_RATE,
    )
    if not allowed:
        release_idempotency(request)
        resp = JsonResponse(
            {"detail": f"触发请求过于频繁，请 {retry_after} 秒后重试"},
            status=429,
        )
        resp["Retry-After"] = str(retry_after)
        return resp

    # 分布式锁：防同一同步配置并发执行（与 /sync/configs/{id}/trigger 同锁名）。
    lock = get_lock(f"sync:config:{sync_config_id}")
    if not lock.acquire():
        release_idempotency(request)
        info = lock.info()
        raise HttpError(409, f"同步配置 {sync_config_id} 正在执行中（锁剩余 {info.ttl}s）")

    task_id = uuid.uuid4().hex

    def _bg_run() -> None:
        """后台线程执行同步：finally 释放锁并关闭本线程的 DB 连接."""
        try:
            SyncService(config).run()
        except SyncError:
            # 同步失败已由 SyncService 内部记录 SyncLog/告警，此处不抛出。
            pass
        finally:
            lock.release()
            connections.close_all()

    threading.Thread(target=_bg_run, daemon=True).start()

    log_audit(
        request,
        action=AuditAction.SYNC_TRIGGER,
        resource_type="dataset",
        resource_id=str(ds.pk),
        extra={
            "slug": ds.slug,
            "sync_config_id": sync_config_id,
            "task_id": task_id,
            "token": token.prefix if token else "",
        },
    )

    body = DatasetSyncTriggerOut(
        task_id=task_id,
        sync_config_id=sync_config_id,
        status="accepted",
    ).model_dump()
    store_idempotency_result(request, 202, body)
    return JsonResponse(body, status=202)


__all__ = ["router"]
