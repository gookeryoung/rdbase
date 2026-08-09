"""webhook app config."""

from __future__ import annotations

from django.apps import AppConfig


class WebhookConfig(AppConfig):
    """Webhook 订阅与投递应用配置."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webhook"
    verbose_name = "Webhook 订阅"
