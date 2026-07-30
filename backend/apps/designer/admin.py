"""designer admin 注册."""

from __future__ import annotations

from django.contrib import admin

from .models import DesignDraft, DesignVersion


@admin.register(DesignDraft)
class DesignDraftAdmin(admin.ModelAdmin):
    """表设计草稿 admin 视图."""

    list_display = (  # type: ignore[bad-override-mutable-attribute]
        "id",
        "name",
        "datasource",
        "table_name",
        "schema_name",
        "status",
        "created_by",
        "updated_at",
    )
    list_filter = ("status", "datasource")  # type: ignore[bad-override-mutable-attribute]
    search_fields = ("name", "table_name", "schema_name")  # type: ignore[bad-override-mutable-attribute]
    readonly_fields = ("spec", "created_at", "updated_at")  # type: ignore[bad-override-mutable-attribute]


@admin.register(DesignVersion)
class DesignVersionAdmin(admin.ModelAdmin):
    """草稿版本 admin 视图."""

    list_display = (  # type: ignore[bad-override-mutable-attribute]
        "id",
        "draft",
        "version_no",
        "created_by",
        "created_at",
    )
    list_filter = ("draft",)  # type: ignore[bad-override-mutable-attribute]
    search_fields = ("draft__name",)  # type: ignore[bad-override-mutable-attribute]
    readonly_fields = ("spec", "created_at")  # type: ignore[bad-override-mutable-attribute]
