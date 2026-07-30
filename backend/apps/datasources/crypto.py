"""凭据加解密工具.

使用 cryptography.fernet.Fernet 对称加密，密钥由 Django SECRET_KEY 派生。
派生方式：SHA256(SECRET_KEY) 取前 32 字节 → urlsafe_base64 编码 → Fernet key。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def derive_key(secret: str) -> bytes:
    """从 SECRET_KEY 派生 Fernet 兼容的 urlsafe base64 密钥."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_password(plaintext: str, secret_key: str) -> str:
    """加密密码，返回 Fernet token 字符串."""
    if not plaintext:
        return ""
    fernet = Fernet(derive_key(secret_key))
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_password(ciphertext: str, secret_key: str) -> str:
    """解密 Fernet token，返回明文密码.

    抛出:
        InvalidToken: 密文损坏或密钥不匹配时抛出。
    """
    if not ciphertext:
        return ""
    fernet = Fernet(derive_key(secret_key))
    plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
    return plaintext.decode("utf-8")


__all__ = ["InvalidToken", "decrypt_password", "derive_key", "encrypt_password"]
