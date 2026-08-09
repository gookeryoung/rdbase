"""Webhook 订阅与投递日志 Pydantic Schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ninja import Schema


class WebhookSubscriptionCreateIn(Schema):
    """Webhook 订阅创建请求.

    Attributes:
        name: 订阅名称（唯一）。
        url: 接收 URL。
        secret: 签名密钥（用于 HMAC-SHA256）。
        events: 订阅事件类型列表，如 ``["sync.completed","ingest.completed"]``。
        is_active: 是否启用，默认 True。
    """

    name: str
    url: str
    secret: str
    events: list[str] = []
    is_active: bool = True


class WebhookSubscriptionUpdateIn(Schema):
    """Webhook 订阅更新请求（所有字段可选；secret 为空表示不更新）."""

    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookSubscriptionOut(Schema):
    """Webhook 订阅响应（不回显 secret）."""

    id: int
    name: str
    url: str
    signing_algorithm: str
    events: list[str]
    is_active: bool
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


class WebhookSubscriptionListOut(Schema):
    """Webhook 订阅列表响应."""

    items: list[WebhookSubscriptionOut]
    total: int


class WebhookDeliveryLogOut(Schema):
    """Webhook 投递日志响应."""

    id: int
    subscription_id: int
    event_type: str
    payload: dict[str, Any]
    status_code: int | None
    retry_count: int
    next_retry_at: datetime | None
    response_body: str
    error_message: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class WebhookDeliveryLogListOut(Schema):
    """Webhook 投递日志列表响应."""

    items: list[WebhookDeliveryLogOut]
    total: int


class MessageOut(Schema):
    """通用消息响应."""

    detail: str
