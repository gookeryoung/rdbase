"""系统设置应用."""

from __future__ import annotations

from django.apps import AppConfig


class SettingsConfig(AppConfig):
    """系统设置 AppConfig."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settings"
    verbose_name = "系统设置"
