"""数据查询模块.

提供表数据浏览的查询与计数能力，基于 SQLAlchemy ``text()`` 手动构造 SQL，
通过列名白名单校验防 SQL 注入，支持分页/排序/筛选/列显隐。

设计要点：
- ``query_table_rows``: 查询表数据，返回 ``(rows, total)``
- ``count_table_rows``: 统计满足筛选条件的行数
- 标识符引用：MySQL 反引号、PG/SQLite 双引号（独立实现，避免跨模块依赖）
- 白名单：通过 SQLAlchemy inspect 获取列名集合，校验 ``columns``/``order_by``/``filters``
- SQLite schema 强制 None
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from apps.datasources.models import EngineType


class QueryError(ValueError):
    """查询错误（如列名非法、操作符不支持、表不存在等）."""


# 支持的筛选操作符 → SQL 比较运算符
# ``in`` 操作符需要展开多个占位符，单独处理
_COMPARATORS: frozenset[str] = frozenset({"eq", "ne", "gt", "lt", "ge", "le", "like", "in"})


def _quote_ident(name: str, dialect: str) -> str:
    """标识符引用（MySQL 用反引号，其他用双引号）."""
    if dialect == EngineType.MYSQL:
        return f"`{name}`"
    return f'"{name}"'


def _format_table_ref(table_name: str, schema: str | None, dialect: str) -> str:
    """生成表引用（含 schema 前缀）.

    SQLite 不支持 schema 限定，强制忽略 schema_name。
    """
    if schema and dialect != EngineType.SQLITE:
        return f"{_quote_ident(schema, dialect)}.{_quote_ident(table_name, dialect)}"
    return _quote_ident(table_name, dialect)


def _resolve_schema(engine: Engine, schema: str | None) -> str | None:
    """SQLite 不支持 schema 概念，强制返回 None；其他方言原样返回."""
    if engine.dialect.name == EngineType.SQLITE:
        return None
    return schema


def get_column_names(engine: Engine, table_name: str, schema: str | None) -> list[str]:
    """通过 SQLAlchemy inspect 获取表的列名列表.

    Raises:
        QueryError: 表不存在或反射失败。
    """
    insp = inspect(engine)
    effective_schema = _resolve_schema(engine, schema)
    try:
        columns = insp.get_columns(table_name, schema=effective_schema)
    except SQLAlchemyError as exc:
        raise QueryError(f"无法读取表 {table_name} 的列信息: {exc}") from None
    return [cast(str, c["name"]) for c in columns]


def _validate_columns(allowed: set[str], columns: list[str] | None) -> list[str]:
    """校验列名是否在白名单内."""
    if not columns:
        return []
    for col in columns:
        if col not in allowed:
            raise QueryError(f"非法列名: {col}")
    return columns


def _validate_order_by(allowed: set[str], order_by: str | None) -> str | None:
    """校验排序字段."""
    if not order_by:
        return None
    if order_by not in allowed:
        raise QueryError(f"非法排序字段: {order_by}")
    return order_by


def _validate_filter_columns(allowed: set[str], filters: dict[str, dict[str, Any]]) -> None:
    """校验筛选条件中的列名与操作符."""
    for col, cond in filters.items():
        if col not in allowed:
            raise QueryError(f"非法筛选列名: {col}")
        op = cond.get("op")
        if op not in _COMPARATORS:
            raise QueryError(f"不支持的操作符: {op}")


def _build_where_clause(
    filters: dict[str, dict[str, Any]],
    dialect: str,
) -> tuple[str, dict[str, Any]]:
    """构造 WHERE 子句与参数.

    Args:
        filters: 筛选条件，格式 ``{列名: {"op": "eq/ne/gt/lt/ge/le/like/in", "val": ...}}``.
        dialect: 方言名。

    Returns:
        ``(where_sql, params)``：``where_sql`` 不含 ``WHERE`` 关键字，空字符串表示无条件。
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, (col, cond) in enumerate(filters.items()):
        op = cond["op"]
        val = cond.get("val")
        col_ref = _quote_ident(col, dialect)
        param_base = f"f{i}"
        if op == "in":
            if not isinstance(val, list) or not val:
                raise QueryError("in 操作符的值须为非空列表")
            placeholders = ", ".join(f":{param_base}_{j}" for j in range(len(val)))
            clauses.append(f"{col_ref} IN ({placeholders})")
            for j, v in enumerate(val):
                params[f"{param_base}_{j}"] = v
        else:
            op_map = {
                "eq": "=",
                "ne": "<>",
                "gt": ">",
                "lt": "<",
                "ge": ">=",
                "le": "<=",
                "like": "LIKE",
            }
            clauses.append(f"{col_ref} {op_map[op]} :{param_base}")
            params[param_base] = val
    return " AND ".join(clauses), params


def query_table_rows(  # noqa: PLR0913, PLR0917
    engine: Engine,
    table_name: str,
    schema: str | None = None,
    columns: list[str] | None = None,
    page: int = 1,
    page_size: int = 20,
    order_by: str | None = None,
    order_dir: str = "asc",
    filters: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """查询表数据，返回 ``(行列表, 总数)``.

    Args:
        engine: SQLAlchemy 引擎。
        table_name: 表名。
        schema: Schema 名（SQLite 强制 None）。
        columns: 显式指定的列名列表；None 或空表示所有列。
        page: 页码，从 1 开始。
        page_size: 每页行数。
        order_by: 排序字段；None 表示不排序。
        order_dir: 排序方向，``asc`` 或 ``desc``。
        filters: 筛选条件，格式 ``{列名: {"op": "eq/ne/gt/lt/ge/le/like/in", "val": ...}}``。

    Returns:
        ``(rows, total)``：``rows`` 为 dict 列表（键为列名），``total`` 为满足筛选条件的总行数。

    Raises:
        QueryError: 列名非法、操作符不支持、表不存在、分页参数非法等。
    """
    if page < 1:
        raise QueryError("page 须 >= 1")
    if page_size < 1:
        raise QueryError("page_size 须 >= 1")
    if order_dir not in ("asc", "desc"):
        raise QueryError("order_dir 须为 asc 或 desc")

    filters = filters or {}
    dialect = engine.dialect.name
    effective_schema = _resolve_schema(engine, schema)
    table_ref = _format_table_ref(table_name, effective_schema, dialect)

    allowed = set(get_column_names(engine, table_name, schema))
    selected_columns = _validate_columns(allowed, columns)
    _validate_order_by(allowed, order_by)
    _validate_filter_columns(allowed, filters)

    # SELECT 列：显式列出避免暴露非预期列；为空时用 *
    if selected_columns:
        select_cols = ", ".join(_quote_ident(c, dialect) for c in selected_columns)
    else:
        select_cols = "*"

    where_sql, params = _build_where_clause(filters, dialect)
    where_clause = f" WHERE {where_sql}" if where_sql else ""

    # 排序
    order_clause = ""
    if order_by:
        order_clause = f" ORDER BY {_quote_ident(order_by, dialect)} {order_dir.upper()}"

    # 分页（SQLite/MySQL/PG 均支持 LIMIT/OFFSET）
    offset = (page - 1) * page_size
    limit_clause = " LIMIT :_page_size OFFSET :_offset"
    params["_page_size"] = page_size
    params["_offset"] = offset

    select_sql = f"SELECT {select_cols} FROM {table_ref}{where_clause}{order_clause}{limit_clause}"
    count_sql = f"SELECT COUNT(*) FROM {table_ref}{where_clause}"

    with engine.connect() as conn:
        rows_result = conn.execute(text(select_sql), params)
        rows = [cast("dict[str, Any]", dict(row._mapping)) for row in rows_result.fetchall()]
        # 在同一连接内统计总数（避免重复反射）
        total = cast(int, conn.execute(text(count_sql), params).scalar())

    return rows, total


def count_table_rows(
    engine: Engine,
    table_name: str,
    schema: str | None = None,
    filters: dict[str, dict[str, Any]] | None = None,
) -> int:
    """统计满足筛选条件的行数.

    Args:
        engine: SQLAlchemy 引擎。
        table_name: 表名。
        schema: Schema 名（SQLite 强制 None）。
        filters: 筛选条件，同 :func:`query_table_rows`。

    Returns:
        总行数。

    Raises:
        QueryError: 列名非法、操作符不支持、表不存在等。
    """
    filters = filters or {}
    dialect = engine.dialect.name
    effective_schema = _resolve_schema(engine, schema)
    table_ref = _format_table_ref(table_name, effective_schema, dialect)

    allowed = set(get_column_names(engine, table_name, schema))
    _validate_filter_columns(allowed, filters)

    where_sql, params = _build_where_clause(filters, dialect)
    where_clause = f" WHERE {where_sql}" if where_sql else ""
    count_sql = f"SELECT COUNT(*) FROM {table_ref}{where_clause}"

    with engine.connect() as conn:
        return cast(int, conn.execute(text(count_sql), params).scalar())


__all__ = [
    "QueryError",
    "count_table_rows",
    "get_column_names",
    "query_table_rows",
]
