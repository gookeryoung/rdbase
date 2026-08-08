"""系统运维应用.

提供深度健康检查、连接池监控、Redis 客户端等系统级能力。
"""

from __future__ import annotations

from django.apps import AppConfig


class SystemConfig(AppConfig):
    """系统运维 AppConfig."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.system"
    verbose_name = "系统运维"
