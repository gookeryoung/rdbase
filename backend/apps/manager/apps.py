"""manager 应用配置."""

from __future__ import annotations

from django.apps import AppConfig


class ManagerConfig(AppConfig):
    """manager 应用配置."""

    default_auto_field = "django.db.models.BigAutoField"  # type: ignore[bad-override]
    name = "apps.manager"
    label = "manager"
    verbose_name = "数据库管理"
