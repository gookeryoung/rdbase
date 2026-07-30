"""datasources admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import DataSource


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    """数据源 admin 视图（密码字段不在列表展示）."""

    list_display = ("id", "name", "engine", "host", "database", "group", "is_active", "created_at")  # type: ignore[bad-override-mutable-attribute]
    list_filter = ("engine", "group", "is_active")  # type: ignore[bad-override-mutable-attribute]
    search_fields = ("name", "host", "database")  # type: ignore[bad-override-mutable-attribute]
    readonly_fields = ("password_encrypted", "created_at", "updated_at")  # type: ignore[bad-override-mutable-attribute]
