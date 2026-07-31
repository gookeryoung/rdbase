"""sync app config."""

from __future__ import annotations

from django.apps import AppConfig


class SyncConfig(AppConfig):
    """数据同步应用配置."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sync"
    verbose_name = "数据同步"
