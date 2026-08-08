"""数据摄取模型 Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import IngestAlert, IngestFieldMapping, IngestLog, IngestTask


class IngestFieldMappingInline(admin.TabularInline):
    """字段映射内联编辑."""

    model = IngestFieldMapping
    extra = 1


@admin.register(IngestTask)
class IngestTaskAdmin(admin.ModelAdmin):
    """爬取任务后台管理."""

    list_display = [
        "name",
        "source_type",
        "status",
        "source_url",
        "target_datasource",
        "target_table",
        "last_sync_at",
        "created_at",
    ]
    list_filter = ["source_type", "status", "target_datasource"]
    search_fields = ["name", "source_url", "target_table"]
    inlines = [IngestFieldMappingInline]


@admin.register(IngestLog)
class IngestLogAdmin(admin.ModelAdmin):
    """爬取日志后台管理."""

    list_display = [
        "task",
        "status",
        "rows_read",
        "rows_written",
        "rows_skipped",
        "duration_ms",
        "started_at",
    ]
    list_filter = ["status", "task"]
    search_fields = ["task__name", "error_message"]
    readonly_fields = [
        "task",
        "status",
        "rows_read",
        "rows_written",
        "rows_skipped",
        "error_message",
        "started_at",
        "finished_at",
        "duration_ms",
    ]
    ordering = ["-started_at"]


@admin.register(IngestAlert)
class IngestAlertAdmin(admin.ModelAdmin):
    """爬取告警后台管理."""

    list_display = ["task", "level", "acknowledged", "created_at"]
    list_filter = ["level", "acknowledged", "task"]
    search_fields = ["task__name", "message"]
    readonly_fields = ["task", "level", "message", "created_at"]
    ordering = ["-created_at"]
