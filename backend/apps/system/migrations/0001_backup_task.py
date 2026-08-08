"""创建 BackupTask 模型."""

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """新建 BackupTask 表，用于跟踪备份/恢复异步任务状态."""

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[("backup", "备份"), ("restore", "恢复")],
                        max_length=8,
                        verbose_name="动作",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "等待中"),
                            ("running", "执行中"),
                            ("success", "成功"),
                            ("failed", "失败"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("archive_name", models.CharField(blank=True, default="", max_length=255, verbose_name="归档文件名")),
                ("archive_size", models.BigIntegerField(blank=True, null=True, verbose_name="归档大小（字节）")),
                ("engine", models.CharField(blank=True, default="", max_length=16, verbose_name="数据库引擎")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误信息")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="完成时间")),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="backup_tasks",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="触发用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "备份任务",
                "verbose_name_plural": "备份任务",
                "ordering": ["-id"],
                "indexes": [
                    models.Index(fields=["action"], name="idx_backup_task_action"),
                    models.Index(fields=["status"], name="idx_backup_task_status"),
                    models.Index(fields=["created_at"], name="idx_backup_task_created"),
                ],
            },
        ),
    ]
