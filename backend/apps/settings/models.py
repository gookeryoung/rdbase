"""系统设置模型.

采用 key-value 存储结构，每个设置项包含 key/value/value_type/description。
value_type 支持 str/int/bool/json，读取时按类型自动反序列化。
预置设置项在数据迁移中初始化（见 apps/settings/management/commands/init_settings.py）。
"""

from __future__ import annotations

import json
from typing import Any

from django.db import models


class ValueType(models.TextChoices):
    """设置值类型枚举."""

    STR = "str", "字符串"
    INT = "int", "整数"
    BOOL = "bool", "布尔"
    JSON = "json", "JSON"


# 预置设置项定义（key → 默认值与类型映射）
# 新增设置项时在此处追加，init_settings 迁移会自动创建
PRESET_SETTINGS: dict[str, tuple[str, str, str]] = {
    # 会话超时
    "session.access_token_minutes": (
        "15",
        ValueType.INT,
        "access token 有效期（分钟）",
    ),
    "session.refresh_token_days": (
        "7",
        ValueType.INT,
        "refresh token 有效期（天）",
    ),
    # 密码策略
    "password.min_length": (
        "8",
        ValueType.INT,
        "密码最小长度",
    ),
    "password.require_uppercase": (
        "false",
        ValueType.BOOL,
        "密码须包含大写字母",
    ),
    "password.require_lowercase": (
        "false",
        ValueType.BOOL,
        "密码须包含小写字母",
    ),
    "password.require_digit": (
        "false",
        ValueType.BOOL,
        "密码须包含数字",
    ),
    "password.require_special": (
        "false",
        ValueType.BOOL,
        "密码须包含特殊字符",
    ),
    "password.history_count": (
        "0",
        ValueType.INT,
        "密码历史检查数量（0 表示不检查）",
    ),
}


class SystemSetting(models.Model):
    """系统设置项.

    采用 key-value 结构，value 始终以字符串存储，读取时按 value_type 反序列化。
    通过 ``get_setting(key)`` 便捷函数获取类型化的设置值。
    """

    objects: models.Manager[SystemSetting]

    key = models.CharField(
        max_length=128,
        unique=True,
        verbose_name="设置键",
        help_text="点分路径格式，如 session.access_token_minutes",
    )
    value = models.TextField(blank=True, default="", verbose_name="设置值（字符串存储）")
    value_type = models.CharField(
        max_length=16,
        choices=ValueType.choices,
        default=ValueType.STR,
        verbose_name="值类型",
    )
    description = models.CharField(max_length=255, blank=True, default="", verbose_name="描述")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"
        ordering = ["key"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回 key = value 摘要."""
        return f"{self.key} = {self.value}"  # type: ignore[bad-return]

    @property
    def typed_value(self) -> Any:
        """按 value_type 反序列化设置值."""
        return _deserialize(self.value, self.value_type)


def _deserialize(value: str, value_type: str) -> Any:
    """按 value_type 将字符串值反序列化为 Python 类型."""
    if value_type == ValueType.INT:
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    if value_type == ValueType.BOOL:
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if value_type == ValueType.JSON:
        try:
            return json.loads(value) if value else None
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def get_setting(key: str, default: Any = None) -> Any:
    """获取系统设置值（按 value_type 反序列化）.

    Args:
        key: 设置键，如 ``session.access_token_minutes``。
        default: 键不存在时返回的默认值。

    Returns:
        类型化的设置值，或 ``default``（键不存在或反序列化失败时）。
    """
    try:
        setting = SystemSetting.objects.get(key=key)
        return setting.typed_value
    except SystemSetting.DoesNotExist:  # type: ignore[missing-attribute]
        return default


def get_setting_int(key: str, default: int = 0) -> int:
    """获取整数类型设置值（便捷函数）."""
    val = get_setting(key, default)
    return int(val) if val is not None else default


def get_setting_bool(key: str, default: bool = False) -> bool:
    """获取布尔类型设置值（便捷函数）."""
    val = get_setting(key, default)
    if isinstance(val, bool):
        return val
    return bool(val) if val is not None else default


__all__ = [
    "PRESET_SETTINGS",
    "SystemSetting",
    "ValueType",
    "get_setting",
    "get_setting_bool",
    "get_setting_int",
]
