"""accounts 模型的 admin 配置."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """自定义 User 的 admin 配置，补充 role 字段管理."""

    list_display = ("username", "email", "role", "is_active", "is_staff", "date_joined")  # type: ignore[bad-override-mutable-attribute]
    list_filter = ("role", "is_active", "is_staff")  # type: ignore[bad-override-mutable-attribute]
    search_fields = ("username", "email")  # type: ignore[bad-override-mutable-attribute]
    fieldsets = (*UserAdmin.fieldsets, ("角色与权限", {"fields": ("role",)}))  # type: ignore[bad-override-mutable-attribute]
    add_fieldsets = (*UserAdmin.add_fieldsets, ("角色与权限", {"fields": ("role",)}))  # type: ignore[bad-override-mutable-attribute]
