"""manager 模块的 Pydantic Schema.

P4-1 数据浏览：行列表响应。
P4-2 数据 CRUD：行新增/更新/查询响应。
P4-3 SQL 控制台：SQL 执行与执行计划响应。
P4-4 导入导出：导入结果响应。
P4-5 对象管理：视图/存储过程/函数/触发器响应。
"""

from __future__ import annotations

from typing import Any, Literal

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


class SqlExportIn(Schema):
    """SQL 结果集导出请求.

    - ``sql``: 原始 SQL（仅允许只读语句 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN，
      后端强制 ``read_only=True`` 拦截 DDL/DML）。
    - ``format``: 导出格式，``csv`` / ``json`` / ``xlsx``。
    """

    sql: str
    format: Literal["csv", "json", "xlsx"] = "csv"


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


class ImportResultOut(Schema):
    """导入结果响应.

    - ``success_count``: 成功插入行数。
    - ``failed_count``: 失败行数（严格事务模式下始终 0，失败时抛错回滚）。
    - ``errors``: 错误信息列表（同上，空列表）。
    """

    success_count: int
    failed_count: int
    errors: list[str]


# ============================================================
# P4-5 对象管理
# ============================================================


class NameOut(Schema):
    """对象名响应（视图/存储过程/函数/触发器列表项）."""

    name: str


class ViewDetailOut(Schema):
    """视图详情响应.

    - ``name``: 视图名。
    - ``schema_name``: Schema 名（SQLite 为 None）。
    - ``definition``: 视图定义 SQL。
    """

    name: str
    schema_name: str | None
    definition: str


class RoutineBriefOut(Schema):
    """存储过程/函数列表项响应.

    - ``name``: 对象名。
    - ``schema_name``: Schema 名。
    - ``type``: ``procedure`` 或 ``function``。
    """

    name: str
    schema_name: str | None
    type: str


class RoutineDetailOut(Schema):
    """存储过程/函数详情响应.

    - ``name``: 对象名。
    - ``schema_name``: Schema 名。
    - ``type``: ``procedure`` 或 ``function``。
    - ``definition``: 定义 SQL。
    """

    name: str
    schema_name: str | None
    type: str
    definition: str


class TriggerBriefOut(Schema):
    """触发器列表项响应.

    - ``name``: 触发器名。
    - ``schema_name``: Schema 名。
    - ``event``: 触发事件（INSERT/UPDATE/DELETE；SQLite 为空）。
    - ``table``: 关联表名。
    - ``timing``: 触发时机（BEFORE/AFTER/INSTEAD OF；SQLite 为空）。
    """

    name: str
    schema_name: str | None
    event: str
    table: str
    timing: str


class TriggerDetailOut(Schema):
    """触发器详情响应.

    - ``name``: 触发器名。
    - ``schema_name``: Schema 名。
    - ``event``: 触发事件。
    - ``table``: 关联表名（编辑/删除时需传入，PG ``DROP TRIGGER ON table`` 需要）。
    - ``timing``: 触发时机。
    - ``definition``: 定义 SQL。
    """

    name: str
    schema_name: str | None
    event: str
    table: str
    timing: str
    definition: str


class ObjectUpdateIn(Schema):
    """对象编辑请求.

    - ``definition``: 完整的 CREATE 语句（CREATE VIEW/CREATE PROCEDURE/CREATE FUNCTION/CREATE TRIGGER）。
    - ``table``: 关联表名（仅触发器编辑/删除时需要，PG ``DROP TRIGGER ON table`` 必需）。
    """

    definition: str
    table: str | None = None


__all__ = [
    "ExplainIn",
    "ExplainOut",
    "ImportResultOut",
    "MessageOut",
    "NameOut",
    "ObjectUpdateIn",
    "RoutineBriefOut",
    "RoutineDetailOut",
    "RowCreateIn",
    "RowListOut",
    "RowOut",
    "RowUpdateIn",
    "SqlExecIn",
    "SqlExportIn",
    "SqlResultOut",
    "TriggerBriefOut",
    "TriggerDetailOut",
    "ViewDetailOut",
]
