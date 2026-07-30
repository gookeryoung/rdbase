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
