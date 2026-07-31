"""settings 应用 Django Admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import SystemSetting


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    """系统设置 Admin 配置."""

    list_display = ("key", "value", "value_type", "description", "updated_at")  # type: ignore[bad-override-mutable-attribute]
    list_filter = ("value_type",)
    search_fields = ("key", "description")
    readonly_fields = ("updated_at",)
    ordering = ("key",)
