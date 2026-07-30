"""designer 应用配置."""

from __future__ import annotations

from django.apps import AppConfig


class DesignerConfig(AppConfig):
    """designer 应用配置."""

    default_auto_field = "django.db.models.BigAutoField"  # type: ignore[bad-override]
    name = "apps.designer"
    label = "designer"
    verbose_name = "数据库设计"
