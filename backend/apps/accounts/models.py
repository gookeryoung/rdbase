"""用户模型."""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


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
