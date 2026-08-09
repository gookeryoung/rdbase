"""Webhook 订阅与投递日志 Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import WebhookDeliveryLog, WebhookSubscription


@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    """Webhook 订阅后台管理."""

    list_display = [
        "name",
        "url",
        "signing_algorithm",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "signing_algorithm"]
    search_fields = ["name", "url"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(WebhookDeliveryLog)
class WebhookDeliveryLogAdmin(admin.ModelAdmin):
    """Webhook 投递日志后台管理."""

    list_display = [
        "subscription",
        "event_type",
        "status_code",
        "retry_count",
        "started_at",
        "duration_ms",
    ]
    list_filter = ["event_type", "status_code", "subscription"]
    search_fields = ["event_type", "error_message", "response_body"]
    readonly_fields = [
        "subscription",
        "event_type",
        "payload",
        "status_code",
        "retry_count",
        "next_retry_at",
        "response_body",
        "error_message",
        "started_at",
        "finished_at",
        "duration_ms",
    ]
    ordering = ["-started_at"]
