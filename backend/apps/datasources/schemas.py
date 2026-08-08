"""datasources 模块的 Pydantic Schema."""

from __future__ import annotations

from typing import Any

from ninja import Schema


class DataSourceCreateIn(Schema):
    """数据源创建请求."""

    name: str
    engine: str  # mysql/postgresql/sqlite
    host: str = ""
    port: int | None = None
    database: str
    username: str = ""
    password: str = ""  # 明文，服务端加密入库
    group: str = "default"
    tags: list[str] = []


class DataSourceUpdateIn(Schema):
    """数据源更新请求（所有字段可选）."""

    name: str | None = None
    engine: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None  # 提供则更新密码
    group: str | None = None
    tags: list[str] | None = None
    is_active: bool | None = None


class DataSourceOut(Schema):
    """数据源响应（不含密码）."""

    id: int
    name: str
    engine: str
    host: str
    port: int | None
    database: str
    username: str
    group: str
    tags: list[str]
    is_active: bool
    created_at: str
    updated_at: str


class TestConnectionIn(Schema):
    """连接测试请求（可携带未保存的临时配置）."""

    engine: str
    host: str = ""
    port: int | None = None
    database: str
    username: str = ""
    password: str = ""


class TestConnectionOut(Schema):
    """连接测试响应."""

    ok: bool
    detail: str


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


class ScanResultOut(Schema):
    """扫描结果响应."""

    directory: str
    scanned: int
    created: list[DataSourceOut]
    skipped: list[str]


# ============================================================
# 数据集（Dataset）相关 Schema
# ============================================================


class DatasetCreateIn(Schema):
    """数据集创建请求."""

    slug: str
    name: str
    description: str = ""
    datasource_id: int
    table_name: str
    schema_name: str = ""
    fields_whitelist: list[str] = []
    filter_expression: dict[str, Any] = {}
    aggregations: dict[str, Any] = {}
    is_active: bool = True


class DatasetUpdateIn(Schema):
    """数据集更新请求（所有字段可选；更新时 version 自增）."""

    slug: str | None = None
    name: str | None = None
    description: str | None = None
    datasource_id: int | None = None
    table_name: str | None = None
    schema_name: str | None = None
    fields_whitelist: list[str] | None = None
    filter_expression: dict[str, Any] | None = None
    aggregations: dict[str, Any] | None = None
    is_active: bool | None = None


class DatasetOut(Schema):
    """数据集响应."""

    id: int
    slug: str
    name: str
    description: str
    datasource_id: int
    table_name: str
    schema_name: str
    fields_whitelist: list[str]
    filter_expression: dict[str, Any]
    aggregations: dict[str, Any]
    owner_id: int | None
    is_active: bool
    version: int
    created_at: str
    updated_at: str


class DatasetListOut(Schema):
    """数据集列表响应."""

    items: list[DatasetOut]
    total: int


class DatasetRowsOut(Schema):
    """数据集行查询响应（与 manager RowListOut 同构）."""

    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    columns: list[str]


class DatasetWriteIn(Schema):
    """数据集写入请求（单行/批量 UPSERT）.

    Attributes:
        rows: 行数据列表，每项为 ``列名 -> 值`` 的 dict。
        conflict_strategy: 主键冲突处理策略（upsert/skip/error），默认 upsert。
        pk_fields: 主键字段名列表；为空时由反射自动推断。
    """

    rows: list[dict[str, Any]]
    conflict_strategy: str = "upsert"
    pk_fields: list[str] | None = None


class DatasetWriteOut(Schema):
    """数据集写入响应."""

    written: int
    skipped: int
    total: int
