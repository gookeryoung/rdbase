"""JWT 工具：access/refresh 双 token 签发与验证.

access token：短时（15 分钟），通过 Authorization: Bearer header 传输，前端内存持有。
refresh token：长时（7 天），通过 HttpOnly cookie 传输，仅用于 /auth/refresh 换发新 access token。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from django.conf import settings

# token 类型标识
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# token 有效期（默认值，可被 SystemSetting 覆盖）
DEFAULT_ACCESS_TOKEN_MINUTES = 15
DEFAULT_REFRESH_TOKEN_DAYS = 7

# 签名算法（HS256，复用 Django SECRET_KEY）
ALGORITHM = "HS256"


def _secret_key() -> str:
    """获取签名密钥（SECRET_KEY 运行时必为 str）."""
    key = settings.SECRET_KEY
    assert key is not None
    return key


def _create_token(user_id: int, role: str, token_type: str, lifetime: timedelta) -> str:
    """签发 JWT 通用实现."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "user_id": user_id,
        "token_type": token_type,
        "exp": now + lifetime,
        "iat": now,
    }
    # access token 携带角色便于前端路由守卫；refresh token 不携带角色
    if token_type == ACCESS_TOKEN_TYPE:
        payload["role"] = role
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


def _access_token_lifetime() -> timedelta:
    """获取 access token 有效期（从 SystemSetting 读取，默认 15 分钟）."""
    try:
        from apps.settings.models import get_setting_int

        minutes = get_setting_int("session.access_token_minutes", DEFAULT_ACCESS_TOKEN_MINUTES)
        if minutes < 1:
            minutes = DEFAULT_ACCESS_TOKEN_MINUTES
        return timedelta(minutes=minutes)
    except Exception:
        return timedelta(minutes=DEFAULT_ACCESS_TOKEN_MINUTES)


def _refresh_token_lifetime() -> timedelta:
    """获取 refresh token 有效期（从 SystemSetting 读取，默认 7 天）."""
    try:
        from apps.settings.models import get_setting_int

        days = get_setting_int("session.refresh_token_days", DEFAULT_REFRESH_TOKEN_DAYS)
        if days < 1:
            days = DEFAULT_REFRESH_TOKEN_DAYS
        return timedelta(days=days)
    except Exception:
        return timedelta(days=DEFAULT_REFRESH_TOKEN_DAYS)


def create_access_token(user_id: int, role: str) -> str:
    """签发 access token（短时，含角色，有效期可配置）."""
    return _create_token(user_id, role, ACCESS_TOKEN_TYPE, _access_token_lifetime())


def create_refresh_token(user_id: int) -> str:
    """签发 refresh token（长时，仅用于换发 access token，有效期可配置）."""
    return _create_token(user_id, "", REFRESH_TOKEN_TYPE, _refresh_token_lifetime())


def decode_token(token: str) -> dict[str, Any]:
    """验证并解码 JWT，失败抛 jwt.PyJWTError."""
    return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
