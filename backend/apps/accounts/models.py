"""用户模型."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import cast

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone as django_timezone


class Role(models.TextChoices):
    """用户角色枚举（RBAC）."""

    ADMIN = "admin", "管理员"
    DESIGNER = "designer", "设计者"
    VIEWER = "viewer", "查看者"


class User(AbstractUser):
    """平台用户模型，扩展 role 字段支持 RBAC."""

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        verbose_name="角色",
    )

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回用户名."""
        return self.username  # type: ignore[bad-return]

    @property
    def is_admin(self) -> bool:
        """是否管理员（拥有全部权限）."""
        return self.role == Role.ADMIN


class PasswordHistory(models.Model):
    """密码历史记录（用于防止密码重复使用）."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_histories")
    password_hash = models.CharField(max_length=128, verbose_name="密码哈希（SHA-256）")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "密码历史"
        verbose_name_plural = "密码历史"
        ordering = ["-created_at"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.user_id}: {self.password_hash[:16]}..."  # type: ignore[bad-return]


# API Token 明文长度（secrets.token_urlsafe 字节数）
_TOKEN_RANDOM_BYTES = 32
# 用于展示识别的前缀长度
_PREFIX_LENGTH = 8


class ApiToken(models.Model):
    """对外 API 访问令牌.

    外部应用通过 ``Authorization: Bearer <token>`` 或 ``X-API-Token: <token>``
    请求头访问数据中心的对外端点。明文 token 仅创建时返回一次，DB 存储 SHA-256
    哈希；泄露后可吊销（``is_active=False``）或轮换（生成新 token 替换旧 token）。

    设计要点：

    - ``token_hash`` 为 SHA-256 十六进制（64 字符），唯一索引加速校验时查询。
    - ``prefix`` 取明文前 8 位，仅用于列表展示识别（无法还原明文）。
    - ``scopes`` 为 JSON 数组，如 ``["datasets:read","datasets:write","sync:trigger"]``。
    - ``expires_at`` 为空表示永不过期；校验时对比当前时间。
    - ``last_used_at`` 在每次成功认证后更新，便于审计 Token 使用情况。
    - ``is_active=False`` 表示已吊销，校验时直接拒绝。
    """

    name = models.CharField(max_length=128, verbose_name="Token 名称")
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="Token 哈希（SHA-256）",
    )
    prefix = models.CharField(max_length=16, verbose_name="Token 前缀（展示用）")
    scopes = models.JSONField(default=list, blank=True, verbose_name="权限范围")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="过期时间")
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name="最后使用时间")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name="创建者",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "API Token"
        verbose_name_plural = "API Token"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by"], name="idx_apitoken_creator"),
            models.Index(fields=["is_active"], name="idx_apitoken_active"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.name} ({self.prefix}...)"  # type: ignore[bad-return]

    # --------------------------------------------------------------
    # 生成与哈希
    # --------------------------------------------------------------

    @staticmethod
    def hash_plaintext(plaintext: str) -> str:
        """计算 token 明文的 SHA-256 哈希（十六进制 64 字符）."""
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    @classmethod
    def generate(
        cls,
        *,
        name: str,
        created_by: User,
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[str, ApiToken]:
        """生成新 API Token.

        使用 ``secrets.token_urlsafe`` 生成 URL 安全的随机明文（约 43 字符），
        仅此一次返回；DB 仅存哈希与前 8 位前缀。

        Args:
            name: Token 名称（人类可读标识）。
            created_by: 创建者用户（管理员）。
            scopes: 权限范围列表，如 ``["datasets:read"]``；None 表示空列表。
            expires_at: 过期时间，None 表示永不过期。

        Returns:
            元组 ``(plaintext, token_obj)``：明文仅此一次返回，调用方应立即
            交付给用户并丢弃；``token_obj`` 为已持久化的模型实例。
        """
        plaintext = secrets.token_urlsafe(_TOKEN_RANDOM_BYTES)
        token_obj = cls.objects.create(
            name=name,
            token_hash=cls.hash_plaintext(plaintext),
            prefix=plaintext[:_PREFIX_LENGTH],
            scopes=scopes or [],
            expires_at=expires_at,
            created_by=created_by,
        )
        return plaintext, token_obj

    def rotate(self) -> str:
        """轮换本 Token：生成新明文并更新哈希/前缀，保持主键与其他字段不变.

        旧明文立即失效（哈希被覆盖）。调用方应将返回的新明文交付给用户。

        Returns:
            新的 token 明文（仅此一次返回）。
        """
        plaintext = secrets.token_urlsafe(_TOKEN_RANDOM_BYTES)
        self.token_hash = self.hash_plaintext(plaintext)
        self.prefix = plaintext[:_PREFIX_LENGTH]
        self.is_active = True
        self.last_used_at = None
        self.save(update_fields=["token_hash", "prefix", "is_active", "last_used_at"])
        return plaintext

    # --------------------------------------------------------------
    # 校验
    # --------------------------------------------------------------

    def is_valid(self) -> bool:
        """检查 Token 是否有效（启用且未过期）."""
        if not self.is_active:
            return False
        return self.expires_at is None or self.expires_at > django_timezone.now()

    def touch_last_used(self) -> None:
        """更新最后使用时间为当前时刻（直接 UPDATE，避免触发 save 钩子）."""
        now = django_timezone.now()
        ApiToken.objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now

    def has_scope(self, scope: str) -> bool:
        """检查 Token 是否拥有指定权限范围."""
        return scope in cast(list[str], self.scopes)
