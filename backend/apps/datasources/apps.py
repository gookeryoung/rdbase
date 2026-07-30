"""datasources 应用配置."""

from __future__ import annotations

from django.apps import AppConfig


class DatasourcesConfig(AppConfig):
    """datasources 应用配置."""

    default_auto_field = "django.db.models.BigAutoField"  # type: ignore[bad-override]
    name = "apps.datasources"
    label = "datasources"
    verbose_name = "数据源管理"
