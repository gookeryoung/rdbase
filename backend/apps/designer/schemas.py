"""designer 模块的 Pydantic Schema.

P3-1 反射相关 Schema：库/Schema/表/字段元数据响应。
P3-2 表设计器 Schema：字段/索引/外键定义、表设计规范、草稿 CRUD、DDL 预览与执行。
"""

from __future__ import annotations

from typing import Any

from ninja import Schema

# ----------------- 元数据反射响应（P3-1） -----------------


class DatabaseOut(Schema):
    """数据库条目响应."""

    name: str


class SchemaOut(Schema):
    """Schema 条目响应."""

    name: str


class TableBriefOut(Schema):
    """表/视图摘要响应（不含字段详情）."""

    name: str
    schema_name: str | None = None


class ColumnOut(Schema):
    """字段元数据响应."""

    name: str
    type: str
    nullable: bool
    default: str | None = None
    autoincrement: bool = False
    comment: str | None = None
    primary_key: bool = False
    unique: bool = False


class IndexOut(Schema):
    """索引元数据响应."""

    name: str
    columns: list[str]
    unique: bool


class ForeignKeyOut(Schema):
    """外键元数据响应."""

    name: str | None
    columns: list[str]
    referred_table: str
    referred_schema: str | None
    referred_columns: list[str]


class TableDetailOut(Schema):
    """表完整元数据响应."""

    name: str
    schema_name: str | None = None
    comment: str | None = None
    columns: list[ColumnOut]
    primary_key: list[str]
    foreign_keys: list[ForeignKeyOut]
    indexes: list[IndexOut]
    unique_constraints: list[list[str]]


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


# ----------------- 表设计器 Schema（P3-2） -----------------


class FieldSpec(Schema):
    """字段定义.

    ``default`` 字段约定为完整 SQL 表达式，如 ``'hello'``、``0``、``CURRENT_TIMESTAMP``，
    生成 DDL 时原样输出到 ``DEFAULT`` 子句，避免方言差异与类型推断复杂度。
    """

    name: str
    type: str
    length: int | None = None
    nullable: bool = True
    default: str | None = None
    comment: str | None = None
    primary_key: bool = False
    unique: bool = False
    autoincrement: bool = False


class IndexSpec(Schema):
    """索引定义."""

    name: str
    columns: list[str]
    unique: bool = False


class ForeignKeySpec(Schema):
    """外键定义."""

    name: str | None = None
    columns: list[str]
    referred_table: str
    referred_columns: list[str]
    on_delete: str = "RESTRICT"


class TableDesignSpec(Schema):
    """完整表设计规范."""

    name: str
    schema_name: str | None = None
    comment: str | None = None
    fields: list[FieldSpec]
    indexes: list[IndexSpec] = []
    foreign_keys: list[ForeignKeySpec] = []


class DraftCreateIn(Schema):
    """草稿创建请求."""

    name: str
    datasource_id: int
    table_name: str
    schema_name: str | None = None
    spec: TableDesignSpec


class DraftUpdateIn(Schema):
    """草稿更新请求（部分字段可选）."""

    name: str | None = None
    table_name: str | None = None
    schema_name: str | None = None
    spec: TableDesignSpec | None = None


class DraftOut(Schema):
    """草稿响应."""

    id: int
    name: str
    datasource_id: int
    table_name: str
    schema_name: str | None
    spec: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


class VersionOut(Schema):
    """版本响应."""

    id: int
    draft_id: int
    version_no: int
    spec: dict[str, Any]
    created_at: str


class DDLPreviewIn(Schema):
    """DDL 预览请求.

    传入 ``old_spec`` 时生成 ALTER 语句；不传时生成 CREATE 语句。
    ``datasource_id`` 用于确定目标方言。
    """

    datasource_id: int
    spec: TableDesignSpec
    old_spec: TableDesignSpec | None = None


class DDLPreviewOut(Schema):
    """DDL 预览响应."""

    statements: list[str]


class DDLExecuteIn(Schema):
    """DDL 执行请求.

    传入 ``old_spec`` 时执行 ALTER；不传时执行 CREATE。
    """

    old_spec: TableDesignSpec | None = None


class DDLExecuteOut(Schema):
    """DDL 执行响应."""

    executed: int
    statements: list[str]


__all__ = [
    "ColumnOut",
    "DDLExecuteIn",
    "DDLExecuteOut",
    "DDLPreviewIn",
    "DDLPreviewOut",
    "DatabaseOut",
    "DraftCreateIn",
    "DraftOut",
    "DraftUpdateIn",
    "FieldSpec",
    "ForeignKeyOut",
    "ForeignKeySpec",
    "IndexOut",
    "IndexSpec",
    "MessageOut",
    "SchemaOut",
    "TableBriefOut",
    "TableDesignSpec",
    "TableDetailOut",
    "VersionOut",
]
