"""RBAC 权限依赖.

基于角色（admin/designer/viewer）的访问控制：
- 路由级用 `auth=JWTAuth()` 完成认证，把用户挂到 `request.auth`；
- 路由级用 `dependencies=[require_roles(Role.ADMIN)]` 做角色校验，不通过抛 403。

示例::

    from apps.accounts.auth import JWTAuth
    from apps.accounts.permissions import require_admin

    @router.get("/admin-only", auth=JWTAuth(), dependencies=[require_admin])
    def admin_only(request: HttpRequest) -> ...: ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from ninja.errors import HttpError

from .models import Role, User

# 类型别名：django-ninja dependency 函数签名
PermissionDependency = Callable[..., Any]


def require_roles(*allowed_roles: str) -> PermissionDependency:
    """工厂：返回依赖函数，校验当前用户角色是否在允许列表中.

    Args:
        allowed_roles: 允许通过的角色字符串（Role 枚举值）。

    Returns:
        django-ninja dependency 函数；未认证返回 401，角色不足返回 403。

    Raises:
        ValueError: 未指定任何允许角色。
    """
    if not allowed_roles:
        raise ValueError("require_roles 至少需要指定一个允许角色")

    # 冻结为元组防止外部修改
    allowed = frozenset(allowed_roles)

    def dependency(request: HttpRequest) -> None:
        """校验当前用户角色."""
        user = getattr(request, "auth", None)
        # JWTAuth 未通过时 django-ninja 已直接返回 401，此处防御性校验
        if user is None or not isinstance(user, User):
            raise HttpError(401, "未认证")
        if user.role not in allowed:
            raise HttpError(403, "权限不足")

    return dependency


# 预构造常用依赖：管理员独占
require_admin: PermissionDependency = require_roles(Role.ADMIN)
# 设计者或管理员（viewer 不可访问）
require_designer_or_admin: PermissionDependency = require_roles(Role.DESIGNER, Role.ADMIN)


__all__ = [
    "PermissionDependency",
    "require_admin",
    "require_designer_or_admin",
    "require_roles",
]
