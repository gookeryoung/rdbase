"""datasources 加密模块单元测试."""

from __future__ import annotations

import pytest
from apps.datasources.crypto import (
    InvalidToken,
    decrypt_password,
    derive_key,
    encrypt_password,
)


def test_derive_key_is_urlsafe_base64_of_sha256() -> None:
    """derive_key 应为 SECRET_KEY 的 SHA256 的 urlsafe base64 编码，长度 44 字节."""
    import base64
    import hashlib

    secret = "my-secret"
    expected = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    assert derive_key(secret) == expected
    assert len(derive_key(secret)) == 44  # 32 字节 -> base64 44 字符


def test_encrypt_decrypt_round_trip() -> None:
    """加密后解密应返回原明文."""
    secret = "test-secret-key"
    plaintext = "p@ssw0rd123"
    token = encrypt_password(plaintext, secret)
    assert token != plaintext
    assert decrypt_password(token, secret) == plaintext


def test_encrypt_empty_returns_empty() -> None:
    """空明文加密应返回空串."""
    assert encrypt_password("", "any") == ""
    assert decrypt_password("", "any") == ""


def test_decrypt_with_wrong_key_raises_invalid_token() -> None:
    """用错误密钥解密应抛 InvalidToken."""
    token = encrypt_password("secret", "key-a")
    with pytest.raises(InvalidToken):
        decrypt_password(token, "key-b")


def test_decrypt_corrupt_token_raises_invalid_token() -> None:
    """损坏的密文应抛 InvalidToken."""
    with pytest.raises(InvalidToken):
        decrypt_password("not-a-valid-token", "any-key")


def test_encrypt_same_plaintext_produces_different_tokens() -> None:
    """同一明文多次加密应产生不同 token（Fernet 内置随机 IV）."""
    secret = "k"
    t1 = encrypt_password("pwd", secret)
    t2 = encrypt_password("pwd", secret)
    assert t1 != t2
    assert decrypt_password(t1, secret) == decrypt_password(t2, secret)
