"""系统设置 Schema（Pydantic）."""

from __future__ import annotations

from ninja import Schema


class SystemSettingOut(Schema):
    """系统设置项响应."""

    id: int
    key: str
    value: str
    value_type: str
    description: str
    updated_at: str


class SystemSettingListOut(Schema):
    """系统设置列表响应."""

    items: list[SystemSettingOut]
    total: int


class SystemSettingUpdateIn(Schema):
    """更新系统设置请求体."""

    value: str
    description: str | None = None


class RotateKeyIn(Schema):
    """加密密钥轮换请求体."""

    confirm: bool = False
    new_key: str = ""


class RotateKeyOut(Schema):
    """加密密钥轮换响应."""

    success: bool
    message: str
    rotated_count: int = 0


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


__all__ = [
    "MessageOut",
    "RotateKeyIn",
    "RotateKeyOut",
    "SystemSettingListOut",
    "SystemSettingOut",
    "SystemSettingUpdateIn",
]
