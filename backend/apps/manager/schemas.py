"""manager 模块的 Pydantic Schema.

P4-1 数据浏览：行列表响应。
P4-2 数据 CRUD：行新增/更新/查询响应。
P4-3 SQL 控制台：SQL 执行与执行计划响应。
"""

from __future__ import annotations

from typing import Any

from ninja import Schema


class RowListOut(Schema):
    """行列表响应.

    ``items`` 为行数据列表（每行是 dict，键为列名）；
    ``columns`` 为实际返回的列名顺序（与 items 中 dict 的键顺序一致）。
    """

    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    columns: list[str]


class RowCreateIn(Schema):
    """行新增请求.

    ``values`` 为列名 → 值的 dict。
    """

    values: dict[str, Any]


class RowUpdateIn(Schema):
    """行更新请求.

    ``values`` 为待更新列名 → 值的 dict（不含主键列）。
    """

    values: dict[str, Any]


class RowOut(Schema):
    """单行响应."""

    row: dict[str, Any]


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


class SqlExecIn(Schema):
    """SQL 执行请求.

    ``sql`` 为原始 SQL 字符串（可含末尾分号；多条语句需调用方拆分后逐条调用）。
    """

    sql: str


class SqlResultOut(Schema):
    """SQL 执行结果响应.

    - ``columns``: SELECT 时为结果集列名；DDL/DML 时为空列表。
    - ``rows``: SELECT 时为结果集行 dict 列表；DDL/DML 时为空列表。
    - ``rowcount``: SELECT 时为结果集行数；DDL/DML 时为影响行数（无法获取时为 -1）。
    - ``elapsed_ms``: 执行耗时（毫秒）。
    - ``read_only``: 实际执行的语句是否为只读。
    """

    columns: list[str]
    rows: list[dict[str, Any]]
    rowcount: int
    elapsed_ms: float
    read_only: bool


class ExplainIn(Schema):
    """执行计划请求.

    - ``sql``: 待分析的 SQL。
    - ``analyze``: 是否实际执行以获取真实统计（PG/MySQL 8.0+ 支持；SQLite 忽略）。
    """

    sql: str
    analyze: bool = False


class ExplainOut(Schema):
    """执行计划响应.

    - ``plan``: 执行计划文本行列表（每行一个字符串）。
    - ``rows``: 结构化行 dict 列表（保留原始列）。
    - ``columns``: 结果列名列表。
    - ``analyze``: 实际是否启用 ANALYZE。
    - ``dialect``: 方言名。
    """

    plan: list[str]
    rows: list[dict[str, Any]]
    columns: list[str]
    analyze: bool
    dialect: str


__all__ = [
    "ExplainIn",
    "ExplainOut",
    "MessageOut",
    "RowCreateIn",
    "RowListOut",
    "RowOut",
    "RowUpdateIn",
    "SqlExecIn",
    "SqlResultOut",
]
