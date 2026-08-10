"""webhook 管理命令测试."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from apps.accounts.models import Role, User
from apps.webhook import deliverer
from apps.webhook.deliverer import _PostResult
from apps.webhook.models import WebhookDeliveryLog, WebhookSubscription
from django.core.management import call_command
from django.utils import timezone


def _make_sub(user: User, *, name: str = "cmd-sub") -> WebhookSubscription:
    """创建订阅."""
    return WebhookSubscription.objects.create(
        name=name,
        url="https://example.com/hook",
        secret="s3cret",
        events=["sync.completed"],
        is_active=True,
        created_by=user,
    )


def _stub_post_success() -> Any:
    """构造始终返回 200 的 _http_post 替身."""

    def _stub(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=200, body="ok", error="")

    return _stub


def _noop_sleep(_delay: float) -> None:
    """空操作 sleep 替身."""
    return None


class TestRetryFailedWebhooksCommand:
    """retry_failed_webhooks 管理命令测试."""

    @pytest.mark.django_db(transaction=True)
    def test_no_pending(self, db: Any) -> None:
        """无到期日志时应输出提示."""
        out = StringIO()
        call_command("retry_failed_webhooks", stdout=out)
        assert "无到期" in out.getvalue()

    @pytest.mark.django_db(transaction=True)
    def test_retries_pending_logs(self, make_user: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """到期日志应被重投并输出摘要."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success())
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        admin = make_user(role=Role.ADMIN)
        sub = _make_sub(admin, name="cmd-retry")
        # 创建两条到期的待重投日志
        for i in range(2):
            WebhookDeliveryLog.objects.create(
                subscription=sub,
                event_type="sync.completed",
                payload={"i": i},
                status_code=503,
                started_at=timezone.now(),
                next_retry_at=timezone.now() - timedelta(seconds=10),
            )

        out = StringIO()
        call_command("retry_failed_webhooks", stdout=out)
        output = out.getvalue()
        assert "Webhook 重投完成" in output
        assert "扫描 2 条" in output
        assert "成功重投 2 条" in output
        # 源日志的 next_retry_at 应被清空
        for log in WebhookDeliveryLog.objects.filter(status_code=503):
            assert log.next_retry_at is None
        # 应创建 2 条新日志（成功，status_code=200）
        assert WebhookDeliveryLog.objects.filter(status_code=200).count() == 2

    @pytest.mark.django_db(transaction=True)
    def test_skips_future_next_retry_at(self, make_user: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """未到期的日志（next_retry_at 在未来）不应被重投."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success())
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        admin = make_user(role=Role.ADMIN)
        sub = _make_sub(admin, name="cmd-future")
        WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="sync.completed",
            payload={},
            status_code=503,
            started_at=timezone.now(),
            next_retry_at=timezone.now() + timedelta(seconds=3600),
        )

        out = StringIO()
        call_command("retry_failed_webhooks", stdout=out)
        assert "无到期" in out.getvalue()

    @pytest.mark.django_db(transaction=True)
    def test_skips_null_next_retry_at(self, make_user: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """next_retry_at 为 None 的日志（成功/已重投）不应被重投."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success())
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        admin = make_user(role=Role.ADMIN)
        sub = _make_sub(admin, name="cmd-null")
        WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="sync.completed",
            payload={},
            status_code=200,
            started_at=timezone.now(),
            next_retry_at=None,
        )

        out = StringIO()
        call_command("retry_failed_webhooks", stdout=out)
        assert "无到期" in out.getvalue()
