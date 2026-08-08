"""执行指定爬取任务的管理命令.

作为 Scrapy 子进程入口，由 :func:`apps.ingest.engine.spawn_ingest` 启动。
Twisted reactor 在本进程内运行，与 Django web 进程隔离。

用法::

    python manage.py run_ingest <task_id>
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ingest.engine import execute_task
from apps.ingest.models import IngestLogStatus, IngestTask


class Command(BaseCommand):
    """执行指定爬取任务（Scrapy 子进程入口）."""

    help = "执行指定爬取任务（Scrapy 子进程入口，由 spawn_ingest 启动）"

    def add_arguments(self, parser: Any) -> None:  # type: ignore[missing-override-decorator]
        """添加位置参数 task_id."""
        parser.add_argument("task_id", type=int, help="爬取任务 ID")

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """加载任务并执行，输出执行摘要."""
        task_id: int = options["task_id"]
        try:
            task = IngestTask.objects.get(pk=task_id)
        except IngestTask.DoesNotExist as exc:
            raise CommandError(f"爬取任务 {task_id} 不存在") from exc

        self.stdout.write(f"开始执行爬取任务: {task.name}（{timezone.now():%Y-%m-%d %H:%M:%S}）")
        log = execute_task(task)

        if log.status == IngestLogStatus.SUCCESS:
            self.stdout.write(
                self.style.SUCCESS(
                    f"爬取完成：读取 {log.rows_read} 行，写入 {log.rows_written} 行，"
                    f"跳过 {log.rows_skipped} 行，耗时 {log.duration_ms}ms"
                )
            )
        elif log.status == IngestLogStatus.PARTIAL:
            self.stdout.write(
                self.style.WARNING(
                    f"爬取部分成功：读取 {log.rows_read} 行，写入 {log.rows_written} 行，耗时 {log.duration_ms}ms"
                )
            )
        else:
            self.stderr.write(self.style.ERROR(f"爬取失败：{log.error_message}（耗时 {log.duration_ms}ms）"))
            raise CommandError(f"爬取任务 {task.name} 失败")
