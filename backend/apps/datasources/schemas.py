"""datasources 模块的 Pydantic Schema."""

from __future__ import annotations

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
