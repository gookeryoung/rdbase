"""audit 应用配置."""

from __future__ import annotations

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """audit 应用配置."""

    default_auto_field = "django.db.models.BigAutoField"  # type: ignore[bad-override]
    name = "apps.audit"
    label = "audit"
    verbose_name = "审计日志"
