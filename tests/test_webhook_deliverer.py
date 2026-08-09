"""Webhook 投递器测试.

覆盖：
- 签名计算（HMAC-SHA256，X-Webhook-Signature 头）。
- 2xx 成功不重试；5xx/4xx/网络异常触发指数退避重试。
- 最多 5 次重试（总尝试 6 次），重试耗尽后写失败 DeliveryLog。
- 退避 sleep 钩子可被 monkeypatch 为空操作。
- ``wait=True`` 时同步等待后台线程完成。
- 无匹配订阅时不发起投递。
- 事件分发匹配订阅的 events 列表。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import pytest
from apps.accounts.models import Role, User
from apps.webhook import deliverer
from apps.webhook.deliverer import _PostResult, deliver_event
from apps.webhook.models import WebhookDeliveryLog, WebhookSubscription
from django.utils import timezone


@pytest.fixture(autouse=True)
def _noop_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """将投递器的退避 sleep 替换为空操作，避免重试测试真实等待."""

    def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr(deliverer, "_backoff_sleep", _noop)


def _make_sub(  # noqa: PLR0913
    user: User,
    *,
    name: str = "sub",
    url: str = "https://example.com/hook",
    secret: str = "topsecret",
    events: list[str] | None = None,
    is_active: bool = True,
) -> WebhookSubscription:
    """创建订阅."""
    return WebhookSubscription.objects.create(
        name=name,
        url=url,
        secret=secret,
        events=events if events is not None else ["sync.completed"],
        is_active=is_active,
        created_by=user,
    )


def _capture_post_calls() -> tuple[list[dict[str, Any]], Callable[..., _PostResult]]:
    """构造一个记录调用参数的 _http_post 替身."""
    calls: list[dict[str, Any]] = []

    def _stub(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        calls.append({"url": url, "body": body, "headers": dict(headers), "timeout": timeout})
        return _PostResult(status_code=200, body="ok", error="")

    return calls, _stub


# ================================================================
# 签名计算
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_signature_hmac_sha256_header(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """投递应在 X-Webhook-Signature 头携带 HMAC-SHA256 签名."""
    admin = make_user(role=Role.ADMIN)
    secret = "topsecret"
    _make_sub(admin, secret=secret)
    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    payload = {"config_id": 1, "status": "success"}
    deliver_event("sync.completed", payload, wait=True)

    assert len(calls) == 1
    headers = calls[0]["headers"]
    body: bytes = calls[0]["body"]
    expected_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature"] == f"sha256={expected_sig}"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Webhook-Event"] == "sync.completed"
    # body 应为 payload 的 JSON 编码
    assert json.loads(body.decode("utf-8")) == payload


# ================================================================
# 重试与退避
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_success_no_retry(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """2xx 成功响应应不触发重试，仅调用一次 _http_post."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)
    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert len(calls) == 1
    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 200
    assert log.retry_count == 0
    assert log.error_message == ""


@pytest.mark.django_db(transaction=True)
def test_retry_on_5xx_then_success(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 失败后应重试，成功后停止重试."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    call_count = {"n": 0}

    def _flaky(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return _PostResult(status_code=500, body="err", error="")
        return _PostResult(status_code=200, body="ok", error="")

    monkeypatch.setattr(deliverer, "_http_post", _flaky)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert call_count["n"] == 3
    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 200
    assert log.retry_count == 2


@pytest.mark.django_db(transaction=True)
def test_retry_on_network_error_then_success(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """网络异常应触发重试，成功后停止."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    call_count = {"n": 0}

    def _flaky(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _PostResult(status_code=None, body="", error="URLError: timeout")
        return _PostResult(status_code=200, body="ok", error="")

    monkeypatch.setattr(deliverer, "_http_post", _flaky)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert call_count["n"] == 2
    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 200
    assert log.retry_count == 1
    assert log.error_message == ""  # 成功后 error 清空


@pytest.mark.django_db(transaction=True)
def test_max_retries_exhausted(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """全部重试失败后 retry_count=5，记录最终失败状态码."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    call_count = {"n": 0}

    def _always_fail(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        call_count["n"] += 1
        return _PostResult(status_code=503, body="unavailable", error="")

    monkeypatch.setattr(deliverer, "_http_post", _always_fail)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    # 1 次首次 + 5 次重试 = 6 次
    assert call_count["n"] == 6
    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 503
    assert log.retry_count == 5
    assert "unavailable" in log.response_body


@pytest.mark.django_db(transaction=True)
def test_backoff_delays_sequence(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """重试应使用指数退避序列 1/2/4/8/16s."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    sleeps: list[float] = []

    def _record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(deliverer, "_backoff_sleep", _record_sleep)

    def _always_fail(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=500, body="", error="")

    monkeypatch.setattr(deliverer, "_http_post", _always_fail)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


# ================================================================
# 事件匹配与后台线程
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_no_matching_subscription_no_call(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """无匹配订阅时不应调用 _http_post."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin, events=["ingest.completed"])

    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert calls == []
    assert WebhookDeliveryLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_inactive_subscription_not_matched(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """is_active=False 的订阅不应被匹配."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin, is_active=False)

    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_multiple_subscriptions_each_delivered(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """多个匹配订阅应各自投递一次."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin, name="sub1", url="https://a.com/h")
    _make_sub(admin, name="sub2", url="https://b.com/h")

    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    assert len(calls) == 2
    urls = {c["url"] for c in calls}
    assert urls == {"https://a.com/h", "https://b.com/h"}
    assert WebhookDeliveryLog.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_wait_true_blocks_until_threads_finish(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """wait=True 时主线程应等待所有投递线程完成，返回后 DeliveryLog 已写入."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)
    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    # wait=True 返回后日志应已写入（无需 sleep 等待）
    assert WebhookDeliveryLog.objects.count() == 1
    assert len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_delivery_log_records_payload_and_timing(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeliveryLog 应记录 payload、started_at、finished_at、duration_ms."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)
    _calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    payload = {"config_id": 42, "status": "success", "rows_written": 100}
    deliver_event("sync.completed", payload, wait=True)

    log = WebhookDeliveryLog.objects.get()
    assert log.payload == payload
    assert log.started_at is not None
    assert log.finished_at is not None
    assert log.duration_ms is not None
    assert log.duration_ms >= 0


@pytest.mark.django_db(transaction=True)
def test_subscription_deleted_during_delivery_no_crash(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """投递过程中订阅被删除应不抛异常（仅记日志跳过）."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)

    def _delete_then_post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        # 模拟投递过程中订阅被删
        WebhookSubscription.objects.filter(pk=sub.pk).delete()
        return _PostResult(status_code=200, body="ok", error="")

    monkeypatch.setattr(deliverer, "_http_post", _delete_then_post)

    # 不应抛异常
    deliver_event("sync.completed", {"k": "v"}, wait=True)

    # DeliveryLog 写入可能因订阅已删（CASCADE）而不存在，但不应崩溃
    # 由于 _deliver_one 在 _http_post 后才创建 DeliveryLog，此时订阅已删，
    # subscription_id 仍可写入（仅存外键 ID，不校验存在）
    assert WebhookDeliveryLog.objects.count() == 0 or WebhookDeliveryLog.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_emit_sync_completed_event_called_from_sync_service(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SyncService._do_run 成功后应调用 deliver_event('sync.completed').

    通过 monkeypatch ``deliver_event`` 捕获调用参数，避免后台线程竞态。
    """
    from apps.sync.sync_service import _emit_sync_completed_event

    admin = make_user(role=Role.ADMIN)

    # 构造一个简单的 SyncLog mock
    from apps.datasources.models import DataSource, EngineType
    from apps.sync.models import SyncConfig, SyncLog, SyncLogStatus, SyncMode, SyncStatus

    ds = DataSource.objects.create(
        name="ds-emit",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    config = SyncConfig.objects.create(
        name="emit-cfg",
        source_table="auth_user",
        target_datasource=ds,
        target_table="ext",
        sync_mode=SyncMode.FULL,
        status=SyncStatus.ACTIVE,
        created_by=admin,
    )
    log = SyncLog.objects.create(
        config=config,
        status=SyncLogStatus.SUCCESS,
        mode=SyncMode.FULL,
        rows_read=10,
        rows_written=8,
        rows_skipped=2,
        started_at=timezone.now(),
        finished_at=timezone.now(),
        duration_ms=100,
    )

    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_deliver(event_type: str, payload: dict[str, Any], **kwargs: Any) -> None:
        captured.append((event_type, payload))

    monkeypatch.setattr("apps.sync.sync_service.deliver_event", _fake_deliver, raising=False)
    # 同步模块内为延迟导入，需 patch apps.webhook.deliverer.deliver_event
    monkeypatch.setattr(deliverer, "deliver_event", _fake_deliver)

    _emit_sync_completed_event(log)

    assert len(captured) == 1
    event_type, delivered_payload = captured[0]
    assert event_type == "sync.completed"
    assert delivered_payload["config_id"] == config.pk
    assert delivered_payload["status"] == SyncLogStatus.SUCCESS
    assert delivered_payload["rows_written"] == 8
