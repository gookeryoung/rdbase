"""accounts JWT 工具测试."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from apps.accounts.jwt import (
    ACCESS_TOKEN_TYPE,
    ALGORITHM,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from django.conf import settings


def test_create_access_token_contains_role_and_type() -> None:
    """access token 应含 user_id、role、token_type=access."""
    token = create_access_token(42, "admin")
    payload = decode_token(token)
    assert payload["user_id"] == 42
    assert payload["role"] == "admin"
    assert payload["token_type"] == ACCESS_TOKEN_TYPE


def test_create_refresh_token_has_no_role() -> None:
    """refresh token 应含 token_type=refresh 且不含 role."""
    token = create_refresh_token(42)
    payload = decode_token(token)
    assert payload["user_id"] == 42
    assert payload["token_type"] == REFRESH_TOKEN_TYPE
    assert "role" not in payload


def test_decode_token_invalid_signature_raises() -> None:
    """用错误密钥签名的 token 解码应抛 PyJWTError."""
    token = jwt.encode({"user_id": 1}, "wrong-secret", algorithm=ALGORITHM)
    with pytest.raises(jwt.PyJWTError):
        decode_token(token)


def test_decode_token_expired_raises() -> None:
    """过期的 token 解码应抛 PyJWTError."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": 1,
        "token_type": ACCESS_TOKEN_TYPE,
        "exp": now - timedelta(seconds=1),
        "iat": now - timedelta(hours=1),
    }
    key = settings.SECRET_KEY
    assert key is not None
    token = jwt.encode(payload, key, algorithm=ALGORITHM)
    with pytest.raises(jwt.PyJWTError):
        decode_token(token)


def test_decode_token_malformed_raises() -> None:
    """非 token 字符串解码应抛 PyJWTError."""
    with pytest.raises(jwt.PyJWTError):
        decode_token("not.a.token")
