"""accounts Router：注册/登录/登出/刷新/me."""

from __future__ import annotations

from typing import Any, cast

import jwt
from django.contrib.auth import authenticate
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from .auth import JWTAuth
from .jwt import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from .models import Role, User
from .schemas import LoginIn, MessageOut, PasswordChangeIn, RefreshOut, RegisterIn, TokenOut, UserOut

router = Router(tags=["auth"])

# refresh token cookie 名称
REFRESH_COOKIE = "refresh_token"
# cookie 有效期（秒）：7 天
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 60 * 60


def _user_dict(user: User) -> dict[str, Any]:
    """构造用户信息字典（供 Schema 序列化）."""
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email or "",
        "role": user.role,
        "is_active": user.is_active,
    }


def _set_refresh_cookie(response: HttpResponse, token: str) -> None:
    """设置 HttpOnly refresh token cookie."""
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )


@router.post("/register", response={201: TokenOut})
def register(request: HttpRequest, payload: RegisterIn) -> HttpResponse:  # noqa: ARG001
    """注册新用户（默认 viewer 角色）."""
    if User.objects.filter(username=payload.username).exists():
        raise HttpError(400, "用户名已存在")
    user = User(username=payload.username, email=payload.email, role=Role.VIEWER)
    user.set_password(payload.password)
    user.save()
    access = create_access_token(user.pk, str(user.role))
    refresh = create_refresh_token(user.pk)
    body = TokenOut(access=access, user=UserOut(**_user_dict(user))).model_dump()
    response = JsonResponse(body, status=201)
    _set_refresh_cookie(response, refresh)
    return response


@router.post("/login", response={200: TokenOut})
def login(request: HttpRequest, payload: LoginIn) -> HttpResponse:
    """登录并签发 access/refresh token."""
    user = authenticate(request, username=payload.username, password=payload.password)
    if user is None:
        raise HttpError(401, "用户名或密码错误")
    user = cast(User, user)
    access = create_access_token(user.pk, str(user.role))
    refresh = create_refresh_token(user.pk)
    body = TokenOut(access=access, user=UserOut(**_user_dict(user))).model_dump()
    response = JsonResponse(body)
    _set_refresh_cookie(response, refresh)
    return response


@router.post("/logout", response={200: dict})
def logout(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    """登出：清除 refresh token cookie."""
    response = JsonResponse({"detail": "已登出"})
    response.delete_cookie(REFRESH_COOKIE)
    return response


@router.post("/refresh", response={200: RefreshOut})
def refresh(request: HttpRequest) -> HttpResponse:
    """用 refresh token cookie 换发新 access token."""
    token = request.COOKIES.get(REFRESH_COOKIE)
    if not token:
        raise HttpError(401, "缺少 refresh token")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HttpError(401, "refresh token 无效") from None
    if payload.get("token_type") != REFRESH_TOKEN_TYPE:
        raise HttpError(401, "token 类型错误")
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise HttpError(401, "token 载荷无效")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(401, "用户不存在") from None
    if not user.is_active:
        raise HttpError(401, "用户已禁用")
    access = create_access_token(user.pk, str(user.role))
    body = RefreshOut(access=access).model_dump()
    return JsonResponse(body)


@router.get("/me", response={200: UserOut}, auth=JWTAuth())
def me(request: HttpRequest) -> HttpResponse:
    """获取当前登录用户信息."""
    user = cast(User, getattr(request, "auth", None))
    body = UserOut(**_user_dict(user)).model_dump()
    return JsonResponse(body)


@router.post("/change-password", response={200: MessageOut}, auth=JWTAuth())
def change_password(request: HttpRequest, payload: PasswordChangeIn) -> HttpResponse:
    """个人中心修改密码：校验旧密码后设置新密码."""
    user = cast(User, getattr(request, "auth", None))
    if not user.check_password(payload.old_password):
        raise HttpError(400, "旧密码错误")
    user.set_password(payload.new_password)
    user.save()
    return JsonResponse({"detail": "密码已修改"})
