"""audit 应用 Django Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """审计日志 Admin 列表配置."""

    list_display = ("id", "username", "action", "source", "status", "method", "path", "datasource_name", "created_at")  # type: ignore[bad-override-mutable-attribute]
    list_filter = ("action", "source", "status", "created_at")
    search_fields = ("username", "path", "sql", "error_message")
    readonly_fields = (
        "user",
        "username",
        "action",
        "source",
        "status",
        "method",
        "path",
        "resource_type",
        "resource_id",
        "datasource_id",
        "datasource_name",
        "sql",
        "row_count",
        "elapsed_ms",
        "ip",
        "user_agent",
        "error_message",
        "extra",
        "created_at",
    )
    ordering = ("-id",)

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]  # noqa: ARG002
        """审计日志不允许在 admin 手动创建."""
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]  # noqa: ARG002
        """审计日志不允许修改."""
        return False
