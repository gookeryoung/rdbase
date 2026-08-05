"""执行到期的定时同步任务的管理命令.

供系统级定时器（Windows 任务计划程序 / Linux cron / 容器 sidecar）周期调用，
例如每分钟执行一次::

    python manage.py run_scheduled_sync

命令内部委托 :meth:`SyncService.run_scheduled`，查找所有到达执行时间
（next_run_at <= now）且启用调度的配置并执行，执行后基于 cron 表达式
滚动更新 next_run_at，形成自动循环。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.sync.sync_service import SyncService


class Command(BaseCommand):
    """执行到期的定时同步任务."""

    help = "执行所有到达执行时间的定时同步配置（供系统定时器周期调用）"

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """查找并执行到期的同步任务，输出执行摘要."""
        result = SyncService.run_scheduled()

        if result.total == 0:
            self.stdout.write("无到期的定时同步任务")
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"定时同步完成：共 {result.total} 个任务，"
                f"成功 {result.succeeded}，失败 {result.failed}，跳过 {result.skipped}"
            )
        )
