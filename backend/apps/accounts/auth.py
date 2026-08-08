"""认证类：JWT 与 API Token 双轨."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jwt
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from ninja.security import HttpBearer

from .jwt import ACCESS_TOKEN_TYPE, decode_token
from .models import ApiToken

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


# X-API-Token 请求头名（与 Authorization: Bearer 并存的外部 Token 入口）
_API_TOKEN_HEADER = "X-API-Token"


class ApiTokenAuth(HttpBearer):
    """API Token 认证：支持 ``X-API-Token`` 头或 ``Authorization: Bearer`` 头.

    按请求头优先级解析：

    1. 优先读取 ``X-API-Token`` 请求头（外部应用惯用形式）；
    2. 若未携带，回退到 ``Authorization: Bearer <token>``（与 JWTAuth 同形式，
       但本类按 Token 哈希查 ApiToken 表，不走 JWT 解码）。

    验证成功后：

    - ``request.auth`` 设为 Token 的创建者（User 实例），与 JWTAuth 行为一致，
      便于 ``require_admin``/``log_audit`` 等下游逻辑复用；
    - ``request.api_token`` 设为 ApiToken 实例，供幂等层（``get_idempotent_subject``）
      识别 Token 主体，自动以 ``token:{prefix}`` 作为幂等 key 主体；
    - 同步刷新 ``last_used_at``，便于审计 Token 使用情况。

    Token 明文仅在创建/轮换时返回一次，DB 存 SHA-256 哈希；校验时对入参明文
    计算哈希后查表，命中且 ``is_valid`` 通过才放行。
    """

    def __call__(self, request: HttpRequest) -> Any:  # type: ignore[missing-override-decorator]
        """按优先级解析请求头并认证.

        优先 ``X-API-Token``；若未携带则回退到 ``Authorization: Bearer``。
        任一头存在但校验失败均返回 None（401），不回退到另一形式，避免认证绕过。
        """
        x_token = request.headers.get(_API_TOKEN_HEADER)
        if x_token:
            return self.authenticate(request, x_token)
        return super().__call__(request)

    def authenticate(self, request: HttpRequest, token: str) -> User | None:  # type: ignore[missing-override-decorator]
        """验证 API Token 明文并返回创建者用户，失败返回 None."""
        token = token.strip()
        if not token:
            return None
        token_hash = ApiToken.hash_plaintext(token)
        try:
            api_token = ApiToken.objects.select_related("created_by").get(token_hash=token_hash)
        except ApiToken.DoesNotExist:  # type: ignore[missing-attribute]
            return None
        if not api_token.is_valid():
            return None
        user = api_token.created_by
        if not user.is_active:
            return None
        # 挂载 token 到 request 供幂等层识别主体（user:{pk} -> token:{prefix}）
        request.api_token = api_token  # type: ignore[attr-defined]
        api_token.touch_last_used()
        return user
