"""同步模型 Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import SyncConfig, SyncFieldMapping, SyncLog


class SyncFieldMappingInline(admin.TabularInline):
    """字段映射内联编辑."""

    model = SyncFieldMapping
    extra = 1


@admin.register(SyncConfig)
class SyncConfigAdmin(admin.ModelAdmin):
    """同步配置后台管理."""

    list_display = [
        "name",
        "sync_mode",
        "status",
        "source_table",
        "target_datasource",
        "target_table",
        "last_sync_at",
        "created_at",
    ]
    list_filter = ["sync_mode", "status", "target_datasource"]
    search_fields = ["name", "source_table", "target_table"]
    inlines = [SyncFieldMappingInline]


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    """同步日志后台管理."""

    list_display = [
        "config",
        "status",
        "mode",
        "rows_read",
        "rows_written",
        "rows_skipped",
        "duration_ms",
        "started_at",
    ]
    list_filter = ["status", "mode", "config"]
    search_fields = ["config__name", "error_message"]
    readonly_fields = [
        "config",
        "status",
        "mode",
        "rows_read",
        "rows_written",
        "rows_skipped",
        "error_message",
        "started_at",
        "finished_at",
        "duration_ms",
    ]
    ordering = ["-started_at"]
