"""ingest app config."""

from __future__ import annotations

from django.apps import AppConfig


class IngestConfig(AppConfig):
    """数据摄取应用配置."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ingest"
    verbose_name = "数据摄取"
