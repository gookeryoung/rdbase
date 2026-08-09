"""数据源模型.

存储外部数据库连接配置，密码字段经 Fernet 加密后存入 password_encrypted。
SQLite 引擎使用文件路径（database 字段），无需 host/port/username/password。

Dataset 模型作为对外稳定契约：外部应用通过 slug 访问数据，配置字段白名单
（列级权限）与过滤条件（行级过滤），变更走 version 自增避免破坏调用方。
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


# ============================================================
# 数据集（Dataset）：对外稳定契约
# ============================================================


class Dataset(models.Model):
    """数据集：对外稳定的数据访问契约.

    外部应用通过 ``slug``（如 ``user-profiles``）访问数据，不感知底层数据源
    ID/表名/字段。Dataset 配置字段白名单与过滤条件，实现「列级权限」与
    「行级过滤」语义：

    - ``fields_whitelist``: 允许返回的列名列表；空列表表示允许全部列。
    - ``filter_expression``: 强制行级过滤条件，与查询时传入的 filters AND 组合；
      同名列以 Dataset 配置为准，防止外部调用方绕过行级过滤。
    - ``aggregations``: 预聚合规则（本期仅存储，不参与查询）。
    - ``version``: 配置变更自增，调用方可据此检测契约是否变化。
    - ``is_active=False`` 时对外查询返回 404，管理端仍可访问。
    """

    objects: models.Manager[Dataset]

    slug = models.SlugField(max_length=128, unique=True, verbose_name="Slug（对外稳定标识）")
    name = models.CharField(max_length=128, verbose_name="名称")
    description = models.TextField(blank=True, default="", verbose_name="描述")
    datasource = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="datasets",
        verbose_name="绑定数据源",
    )
    table_name = models.CharField(max_length=128, verbose_name="表名")
    schema_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name="Schema 名（SQLite 留空）",
    )
    fields_whitelist = models.JSONField(
        default=list,
        blank=True,
        verbose_name="字段白名单（空表示全部）",
    )
    filter_expression = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="行级过滤条件",
    )
    aggregations = models.JSONField(default=dict, blank=True, verbose_name="预聚合规则")
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="datasets",
        verbose_name="负责人",
    )
    # 绑定的同步配置（可选）；外部 API 触发 /sync 时按此关联执行
    sync_config = models.ForeignKey(
        "sync.SyncConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="datasets",
        verbose_name="绑定同步配置",
    )
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    version = models.PositiveIntegerField(default=1, verbose_name="版本号（变更自增）")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "数据集"
        verbose_name_plural = "数据集"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["is_active"], name="idx_dataset_active"),
            models.Index(fields=["datasource"], name="idx_dataset_ds"),
            models.Index(fields=["owner"], name="idx_dataset_owner"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.slug} ({self.name})"  # type: ignore[bad-return]

    def increment_version(self) -> None:
        """版本号自增 1（更新场景调用，save 前调用）."""
        self.version = (self.version or 1) + 1
