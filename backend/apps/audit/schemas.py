"""审计日志 Schema（Pydantic）."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ninja import Schema


class AuditLogOut(Schema):
    """审计日志响应 Schema."""

    id: int
    user_id: int | None = None
    username: str
    action: str
    source: str
    status: str
    method: str
    path: str
    resource_type: str
    resource_id: str
    datasource_id: int | None = None
    datasource_name: str
    sql: str
    row_count: int | None = None
    elapsed_ms: int | None = None
    ip: str | None = None
    user_agent: str
    error_message: str
    extra: dict[str, Any]
    created_at: datetime


class AuditLogListOut(Schema):
    """审计日志分页列表 Schema."""

    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int


class AuditLogDetailOut(AuditLogOut):
    """审计日志详情 Schema（与列表项一致，单独定义便于后续扩展）."""


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


__all__ = [
    "AuditLogDetailOut",
    "AuditLogListOut",
    "AuditLogOut",
    "MessageOut",
]
