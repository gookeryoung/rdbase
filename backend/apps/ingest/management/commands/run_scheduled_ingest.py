"""执行到期的定时爬取任务的管理命令.

供系统级定时器（Linux cron / 容器 sidecar）周期调用，例如每分钟执行一次::

    python manage.py run_scheduled_ingest

命令查找所有到达执行时间（next_run_at <= now）且启用调度的爬取任务，
对每个任务以子进程（:func:`apps.ingest.engine.spawn_ingest`）启动 ``run_ingest``
执行，执行后基于 cron 表达式滚动更新 next_run_at，形成自动循环。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingest.engine import spawn_ingest
from apps.ingest.models import IngestStatus, IngestTask


class Command(BaseCommand):
    """执行到期的定时爬取任务."""

    help = "执行所有到达执行时间的定时爬取任务（供系统定时器周期调用）"

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """查找并执行到期的爬取任务，输出执行摘要."""
        now = timezone.now()
        tasks = list(
            IngestTask.objects.filter(
                scheduler_enabled=True,
                status=IngestStatus.ACTIVE,
                next_run_at__lte=now,
            )
        )

        if not tasks:
            self.stdout.write("无到期的定时爬取任务")
            return

        total = len(tasks)
        succeeded = 0
        failed = 0

        for task in tasks:
            result = spawn_ingest(task.pk)
            if result.returncode == 0:
                succeeded += 1
            else:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"任务 {task.name} 执行失败（exit={result.returncode}）: {result.stderr.strip()[:200]}"
                    )
                )
            # 基于 cron 滚动下次执行时间；不可调度或 cron 非法则清空 next_run_at
            task.refresh_next_run()

        self.stdout.write(self.style.SUCCESS(f"定时爬取完成：共 {total} 个任务，成功 {succeeded}，失败 {failed}"))
