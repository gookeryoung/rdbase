"""accounts 应用配置."""

from __future__ import annotations

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """accounts 应用配置."""

    default_auto_field = "django.db.models.BigAutoField"  # type: ignore[bad-override]
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "用户与权限"
