"""manager 模块 Router - 数据浏览接口.

P4-1 数据浏览：基于 SQLAlchemy 反射读取目标库表数据。
- 行列表查询：所有登录用户可读。
"""

from __future__ import annotations

import json
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError
from sqlalchemy.exc import SQLAlchemyError

from apps.accounts.auth import JWTAuth
from apps.datasources.engine import get_engine
from apps.datasources.models import DataSource

from .query import QueryError, get_column_names, query_table_rows
from .schemas import RowListOut

router = Router(tags=["manager"], auth=JWTAuth())


def _get_ds_or_404(ds_id: int) -> DataSource:
    """按 ID 获取数据源，不存在抛 404."""
    try:
        return DataSource.objects.get(pk=ds_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "数据源不存在") from None


def _parse_filters(filters_param: str | None) -> dict[str, dict[str, Any]]:
    """解析 filters JSON 字符串参数为 dict.

    Args:
        filters_param: URL 中的 filters 参数（JSON 字符串），如
            ``'{"name":{"op":"like","val":"J%"}}'``。

    Returns:
        解析后的筛选条件 dict；输入为空时返回空 dict。

    Raises:
        HttpError: JSON 解析失败或结构非法。
    """
    if not filters_param:
        return {}
    try:
        parsed = json.loads(filters_param)
    except json.JSONDecodeError as exc:
        raise HttpError(400, f"filters 参数非法 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise HttpError(400, "filters 须为 JSON 对象")
    return cast("dict[str, dict[str, Any]]", parsed)


def _parse_columns(columns_param: str | None) -> list[str] | None:
    """解析 columns 逗号分隔字符串为列表.

    输入为空时返回 None（表示查询所有列）；非空时按逗号分割并去除空白。
    """
    if not columns_param:
        return None
    cols = [c.strip() for c in columns_param.split(",") if c.strip()]
    return cols or None


@router.get("/{ds_id}/tables/{table_name}/rows", response={200: RowListOut})
def list_rows_view(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
    page: int = 1,
    page_size: int = 20,
    order_by: str | None = None,
    order_dir: str = "asc",
    columns: str | None = None,
    filters: str | None = None,
) -> HttpResponse:
    """查询表行数据（所有登录用户）.

    Query 参数：
        schema_name: Schema 名（SQLite 自动忽略）。
        page: 页码，从 1 开始，默认 1。
        page_size: 每页行数，默认 20。
        order_by: 排序字段（须为表内列名），默认不排序。
        order_dir: 排序方向，``asc`` 或 ``desc``，默认 ``asc``。
        columns: 逗号分隔的列名列表，控制 SELECT 列；为空时返回所有列。
        filters: JSON 字符串，格式 ``{"列名":{"op":"eq/ne/gt/lt/ge/le/like/in","val":...}}``。
    """
    del request  # 仅认证用
    ds = _get_ds_or_404(ds_id)
    parsed_filters = _parse_filters(filters)
    parsed_columns = _parse_columns(columns)
    effective_schema = schema_name or None
    try:
        engine = get_engine(ds)
        rows, total = query_table_rows(
            engine,
            table_name=table_name,
            schema=effective_schema,
            columns=parsed_columns,
            page=page,
            page_size=page_size,
            order_by=order_by,
            order_dir=order_dir,
            filters=parsed_filters,
        )
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"查询失败: {exc}") from None

    # 实际返回的列名顺序
    returned_columns: list[str]
    if parsed_columns:
        returned_columns = parsed_columns
    elif rows:
        returned_columns = list(rows[0].keys())
    else:
        # 无数据时通过反射获取列名（保证 columns 字段始终非空，便于前端渲染表头）
        try:
            returned_columns = get_column_names(engine, table_name, effective_schema)
        except QueryError:
            returned_columns = []

    body = RowListOut(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        columns=returned_columns,
    ).model_dump()
    return JsonResponse(body)


__all__ = ["router"]
