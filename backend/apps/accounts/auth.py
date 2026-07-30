"""JWT 认证类."""

from __future__ import annotations

from typing import TYPE_CHECKING

import jwt
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from ninja.security import HttpBearer

from .jwt import ACCESS_TOKEN_TYPE, decode_token

if TYPE_CHECKING:
    from .models import User


class JWTAuth(HttpBearer):
    """从 Authorization: Bearer <token> 读取 access token 并验证.

    验证成功后将用户对象挂到 request.auth，供视图使用。
    """

    def authenticate(self, request: HttpRequest, token: str) -> User | None:  # noqa: ARG002  # type: ignore[missing-override-decorator]
        """验证 access token 并返回用户，失败返回 None."""
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            return None
        if payload.get("token_type") != ACCESS_TOKEN_TYPE:
            return None
        user_id = payload.get("user_id")
        if not isinstance(user_id, int):
            return None
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        if not user.is_active:
            return None
        return user
