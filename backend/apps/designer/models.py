"""设计草稿模型.

存储用户在表设计器中编辑的草稿与历史版本，平台库中保存；
草稿经 DDL 执行后转为 applied 状态，但保留草稿用于回溯与版本对比。

数据结构约定：
- ``DesignDraft.spec`` 为 ``TableDesignSpec`` 的 JSON 序列化结果（dict）；
- ``DesignVersion.spec`` 为草稿在某个保存时刻的快照（dict）；
- ``DesignVersion.version_no`` 按 draft 维度自增（由 API 层在保存时计算）。
"""

from __future__ import annotations

from django.db import models

from apps.accounts.models import User
from apps.datasources.models import DataSource


class DraftStatus(models.TextChoices):
    """草稿状态."""

    DRAFT = "draft", "草稿"
    APPLIED = "applied", "已应用"


class DesignDraft(models.Model):
    """表设计草稿.

    每条草稿对应一张目标表的设计；spec 为 TableDesignSpec 的 JSON 序列化结果。
    """

    # 显式声明 manager 供类型检查识别
    objects: models.Manager[DesignDraft]

    name = models.CharField(max_length=128, verbose_name="草稿名称")
    datasource = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="drafts",
        verbose_name="目标数据源",
    )
    table_name = models.CharField(max_length=128, verbose_name="目标表名")
    schema_name = models.CharField(max_length=128, blank=True, default="", verbose_name="目标 schema")
    spec = models.JSONField(default=dict, verbose_name="表设计规范")
    status = models.CharField(
        max_length=20,
        choices=DraftStatus.choices,
        default=DraftStatus.DRAFT,
        verbose_name="状态",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="design_drafts",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "表设计草稿"
        verbose_name_plural = "表设计草稿"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["datasource", "table_name", "schema_name"],
                name="unique_draft_per_table",
            ),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回草稿名称."""
        return self.name  # type: ignore[bad-return]


class DesignVersion(models.Model):
    """表设计草稿的历史版本快照."""

    # 显式声明 manager 供类型检查识别
    objects: models.Manager[DesignVersion]

    draft = models.ForeignKey(
        DesignDraft,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="所属草稿",
    )
    version_no = models.PositiveIntegerField(verbose_name="版本号")
    spec = models.JSONField(default=dict, verbose_name="版本快照")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="design_versions",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "草稿版本"
        verbose_name_plural = "草稿版本"
        ordering = ["-version_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "version_no"],
                name="unique_version_no_per_draft",
            ),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回版本标识."""
        return f"{self.draft.name} v{self.version_no}"  # type: ignore[bad-return]


__all__ = ["DesignDraft", "DesignVersion", "DraftStatus"]
