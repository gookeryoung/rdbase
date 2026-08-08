"""system 应用 Django Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import BackupTask


@admin.register(BackupTask)
class BackupTaskAdmin(admin.ModelAdmin):
    """备份任务 Admin（只读，仅查看任务状态与错误信息）."""

    list_display = ("id", "action", "status", "requested_by", "archive_name", "created_at", "completed_at")
    list_filter = ("action", "status")
    search_fields = ("archive_name", "error_message")
    readonly_fields = (
        "requested_by",
        "action",
        "status",
        "archive_name",
        "archive_size",
        "engine",
        "error_message",
        "created_at",
        "completed_at",
    )

    def has_add_permission(self, request: object | None) -> bool:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """禁止在 Admin 手动创建备份任务."""
        return False

    def has_change_permission(self, request: object | None, obj: object | None = None) -> bool:  # type: ignore[missing-override-decorator, override]  # noqa: ARG002
        """禁止修改备份任务."""
        return False
