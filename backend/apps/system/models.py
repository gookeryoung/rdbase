"""system 应用 ORM 模型.

当前持有 ``BackupTask``：备份/恢复异步任务记录，供 ``/system/backup`` 与
``/system/restore`` API 跟踪任务状态。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class BackupTask(models.Model):
    """备份/恢复任务记录.

    由 ``POST /system/backup`` 与 ``POST /system/restore`` 创建，后台线程执行，
    ``GET /system/backup-tasks/{id}`` 查询状态。任务状态机：
    ``pending → running → success/failed``。
    """

    class Action(models.TextChoices):
        """任务动作枚举."""

        BACKUP = "backup", "备份"
        RESTORE = "restore", "恢复"

    class Status(models.TextChoices):
        """任务状态枚举."""

        PENDING = "pending", "等待中"
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backup_tasks",
        verbose_name="触发用户",
    )
    action = models.CharField(
        max_length=8,
        choices=Action.choices,
        verbose_name="动作",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="状态",
    )
    # 归档文件名（备份完成后填充；恢复任务记录 pre-restore 快照名）
    archive_name = models.CharField(max_length=255, blank=True, default="", verbose_name="归档文件名")
    archive_size = models.BigIntegerField(null=True, blank=True, verbose_name="归档大小（字节）")
    engine = models.CharField(max_length=16, blank=True, default="", verbose_name="数据库引擎")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    class Meta:
        verbose_name = "备份任务"
        verbose_name_plural = "备份任务"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["action"], name="idx_backup_task_action"),
            models.Index(fields=["status"], name="idx_backup_task_status"),
            models.Index(fields=["created_at"], name="idx_backup_task_created"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回简要描述."""
        return f"[{self.created_at:%Y-%m-%d %H:%M:%S}] {self.action} {self.status}"  # type: ignore[bad-return]


__all__ = ["BackupTask"]
