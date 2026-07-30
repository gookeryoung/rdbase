"""数据源模型.

存储外部数据库连接配置，密码字段经 Fernet 加密后存入 password_encrypted。
SQLite 引擎使用文件路径（database 字段），无需 host/port/username/password。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.accounts.models import User

from .crypto import decrypt_password, encrypt_password


class EngineType(models.TextChoices):
    """数据源引擎类型枚举."""

    MYSQL = "mysql", "MySQL"
    POSTGRESQL = "postgresql", "PostgreSQL"
    SQLITE = "sqlite", "SQLite"


class DataSource(models.Model):
    """数据源连接配置.

    密码以 Fernet 对称加密存储；SQLite 引擎仅用 database 字段表示文件路径。
    """

    # 显式声明 manager 供类型检查识别（Django 运行时会自动注入默认 manager）
    objects: models.Manager[DataSource]

    name = models.CharField(max_length=128, unique=True, verbose_name="名称")
    engine = models.CharField(
        max_length=20,
        choices=EngineType.choices,
        verbose_name="引擎类型",
    )
    host = models.CharField(max_length=255, blank=True, default="", verbose_name="主机")
    port = models.IntegerField(null=True, blank=True, verbose_name="端口")
    database = models.CharField(max_length=255, verbose_name="数据库/文件路径")
    username = models.CharField(max_length=128, blank=True, default="", verbose_name="用户名")
    password_encrypted = models.TextField(blank=True, default="", verbose_name="加密密码")
    group = models.CharField(max_length=64, blank=True, default="default", verbose_name="分组")
    tags = models.JSONField(default=list, blank=True, verbose_name="标签")
    is_active = models.BooleanField(default=True, verbose_name="启用")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="datasources",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "数据源"
        verbose_name_plural = "数据源"
        ordering = ["-id"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回数据源名称."""
        return self.name  # type: ignore[bad-return]

    @property
    def is_sqlite(self) -> bool:
        """是否 SQLite 引擎（无需 host/port/credentials）."""
        return self.engine == EngineType.SQLITE

    def set_password(self, raw_password: str) -> None:
        """加密并保存密码."""
        key = _get_secret_key()
        self.password_encrypted = encrypt_password(raw_password, key)  # type: ignore[bad-assignment]

    def get_password(self) -> str:
        """解密并返回密码；未设置时返回空串."""
        if not self.password_encrypted:
            return ""
        key = _get_secret_key()
        return decrypt_password(self.password_encrypted, key)  # type: ignore[bad-argument-type]


def _get_secret_key() -> str:
    """读取 Django SECRET_KEY 作为加密密钥源."""
    key = getattr(settings, "SECRET_KEY", None)
    if not key:  # pragma: no cover - Django settings 必有 SECRET_KEY，不可达分支
        raise RuntimeError("SECRET_KEY 未配置，无法加解密数据源密码")
    return key
