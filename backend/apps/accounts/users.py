"""用户管理 Router（管理员独占）.

提供用户列表、启用/禁用、重置密码、修改角色接口，全部要求 admin 角色。
通过 Router 级别 `auth=JWTAuth()` 统一认证，每个路由体首行调用 `require_admin(request)` 校验权限。
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from .auth import JWTAuth
from .models import Role, User
from .permissions import require_admin
from .schemas import MessageOut, PasswordResetIn, RoleUpdateIn, UserOut

# Router 级别统一认证：所有用户管理接口需登录
router = Router(tags=["users"], auth=JWTAuth())


def _user_dict(user: User) -> dict[str, Any]:
    """构造用户信息字典（供 Schema 序列化）."""
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email or "",
        "role": user.role,
        "is_active": user.is_active,
    }


def _get_user_or_404(user_id: int) -> User:
    """按主键获取用户，不存在抛 404."""
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "用户不存在") from None


@router.get("", response={200: list[UserOut]})
def list_users(request: HttpRequest) -> HttpResponse:
    """获取全部用户列表（仅管理员）."""
    require_admin(request)
    users = User.objects.all().order_by("id")
    body = [UserOut(**_user_dict(u)).model_dump() for u in users]
    return JsonResponse(body, safe=False)


@router.post("/{user_id}/toggle-active", response={200: UserOut})
def toggle_active(request: HttpRequest, user_id: int) -> HttpResponse:
    """切换用户启用/禁用状态（仅管理员）."""
    require_admin(request)
    user = _get_user_or_404(user_id)
    user.is_active = not user.is_active  # type: ignore[bad-assignment]
    user.save()
    body = UserOut(**_user_dict(user)).model_dump()
    return JsonResponse(body)


@router.post("/{user_id}/reset-password", response={200: MessageOut})
def reset_password(request: HttpRequest, user_id: int, payload: PasswordResetIn) -> HttpResponse:
    """重置用户密码（仅管理员）."""
    require_admin(request)
    user = _get_user_or_404(user_id)
    user.set_password(payload.new_password)
    user.save()
    return JsonResponse({"detail": "密码已重置"})


@router.patch("/{user_id}/role", response={200: UserOut})
def update_role(request: HttpRequest, user_id: int, payload: RoleUpdateIn) -> HttpResponse:
    """修改用户角色（仅管理员）."""
    require_admin(request)
    if payload.role not in Role.values:
        raise HttpError(400, "角色无效")
    user = _get_user_or_404(user_id)
    user.role = payload.role  # type: ignore[bad-assignment]
    user.save()
    body = UserOut(**_user_dict(user)).model_dump()
    return JsonResponse(body)
