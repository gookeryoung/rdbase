"""重投到期待调度 Webhook 投递日志的管理命令.

供系统级定时器（Windows 任务计划程序 / Linux cron / 容器 sidecar）周期调用，
例如每分钟执行一次::

    python manage.py retry_failed_webhooks

命令扫描所有 ``next_retry_at`` 非空且已到期（``<= now``）的投递日志，
对每条调 :func:`apps.webhook.deliverer.redeliver` 重新投递。
``redeliver`` 会清源日志 ``next_retry_at`` 并创建新日志（成功时 next_retry_at
为 None，失败时设为 ``now + _SCHEDULED_RETRY_INTERVAL`` 形成自动循环）。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.webhook.deliverer import redeliver
from apps.webhook.models import WebhookDeliveryLog


class Command(BaseCommand):
    """重投到期的待调度 Webhook 投递日志."""

    help = "扫描并重投所有到期待调度重试的 Webhook 投递日志（供系统定时器周期调用）"

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """扫描到期日志并重投，输出执行摘要."""
        now = timezone.now()
        pending = list(WebhookDeliveryLog.objects.filter(next_retry_at__isnull=False, next_retry_at__lte=now))
        total = len(pending)
        if total == 0:
            self.stdout.write("无到期的待调度 Webhook 重投任务")
            return

        succeeded = 0
        failed = 0
        for log in pending:
            new_log = redeliver(log.pk)
            if new_log is not None:
                succeeded += 1
            else:
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(f"Webhook 重投完成：扫描 {total} 条，成功重投 {succeeded} 条，失败 {failed} 条")
        )
