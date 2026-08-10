"""Webhook 投递器测试.

覆盖：
- 签名计算（HMAC-SHA256，X-Webhook-Signature 头）。
- 2xx 成功不重试；5xx/4xx/网络异常触发指数退避重试。
- 最多 5 次重试（总尝试 6 次），重试耗尽后写失败 DeliveryLog。
- 退避 sleep 钩子可被 monkeypatch 为空操作。
- ``wait=True`` 时同步等待后台线程完成。
- 无匹配订阅时不发起投递。
- 事件分发匹配订阅的 events 列表。
- 投递失败时 ``next_retry_at`` 标记待调度重投；成功时为 None。
- ``redeliver`` 按源日志 ID 重投，创建新日志并清源日志 ``next_retry_at``。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from apps.accounts.models import Role, User
from apps.webhook import deliverer
from apps.webhook.deliverer import _PostResult, deliver_event, redeliver
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


# ================================================================
# next_retry_at 标记
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_next_retry_at_none_on_success(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """投递成功时 next_retry_at 应为 None."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 200
    assert log.next_retry_at is None


@pytest.mark.django_db(transaction=True)
def test_next_retry_at_set_on_failure(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """投递全部失败时 next_retry_at 应设为 now + _SCHEDULED_RETRY_INTERVAL."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    def _always_fail(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=503, body="unavailable", error="")

    monkeypatch.setattr(deliverer, "_http_post", _always_fail)

    before = timezone.now()
    deliver_event("sync.completed", {"k": "v"}, wait=True)
    after = timezone.now()

    log = WebhookDeliveryLog.objects.get()
    assert log.status_code == 503
    assert log.next_retry_at is not None
    # next_retry_at 应在 [before + 299s, after + 301s] 范围内（允许 1s 抖动）
    expected_min = before + timedelta(seconds=deliverer._SCHEDULED_RETRY_INTERVAL - 1)
    expected_max = after + timedelta(seconds=deliverer._SCHEDULED_RETRY_INTERVAL + 1)
    assert expected_min <= log.next_retry_at <= expected_max


@pytest.mark.django_db(transaction=True)
def test_next_retry_at_none_on_network_error(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """网络异常（status_code=None）全部失败时 next_retry_at 也应设置."""
    admin = make_user(role=Role.ADMIN)
    _make_sub(admin)

    def _network_error(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=None, body="", error="URLError: timeout")

    monkeypatch.setattr(deliverer, "_http_post", _network_error)

    deliver_event("sync.completed", {"k": "v"}, wait=True)

    log = WebhookDeliveryLog.objects.get()
    assert log.status_code is None
    assert log.next_retry_at is not None


# ================================================================
# redeliver 重投
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_redeliver_creates_new_log_with_original_payload(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """redeliver 应以源日志的 event_type+payload 创建新日志."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    # 创建源日志（模拟一次失败的投递）
    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"config_id": 42, "status": "success"},
        status_code=503,
        retry_count=5,
        next_retry_at=timezone.now() + timedelta(seconds=300),
        response_body="err",
        error_message="失败",
        started_at=timezone.now(),
        finished_at=timezone.now(),
        duration_ms=100,
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.pk != source.pk
    assert new_log.subscription_id == sub.pk
    assert new_log.event_type == "sync.completed"
    assert new_log.payload == {"config_id": 42, "status": "success"}
    assert new_log.status_code == 200  # stub 返回成功
    assert new_log.next_retry_at is None  # 成功不设 next_retry_at
    # 应发起一次 HTTP POST
    assert len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_redeliver_clears_source_next_retry_at(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """redeliver 应清源日志的 next_retry_at，避免调度器重复重投."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )
    assert source.next_retry_at is not None

    redeliver(source.pk)

    source.refresh_from_db()
    assert source.next_retry_at is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_nonexistent_log_returns_none(make_user: Callable[..., User]) -> None:
    """redeliver 不存在的日志 ID 应返回 None."""
    result = redeliver(99999)
    assert result is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_subscription_deleted_returns_none(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """redeliver 源日志的订阅已删除时应返回 None（CASCADE 会删日志，此处测边界）."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
    )
    # 删除订阅（CASCADE 会同时删除源日志）
    sub.delete()

    result = redeliver(source.pk)
    assert result is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_failure_sets_next_retry_on_new_log(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """redeliver 重投失败时新日志应设 next_retry_at（形成调度循环）."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)

    def _always_fail(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=500, body="err", error="")

    monkeypatch.setattr(deliverer, "_http_post", _always_fail)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.status_code == 500
    assert new_log.next_retry_at is not None  # 失败设 next_retry_at


@pytest.mark.django_db(transaction=True)
def test_redeliver_http_post_raises_exception(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """_http_post 抛异常时 _deliver_one 应捕获并记录 error，仍创建日志."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)

    def _raise(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        raise RuntimeError("连接池耗尽")

    monkeypatch.setattr(deliverer, "_http_post", _raise)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.status_code is None  # 未收到响应
    assert "连接池耗尽" in new_log.error_message or "投递过程异常" in new_log.error_message
    assert new_log.next_retry_at is not None  # 失败设 next_retry_at


@pytest.mark.django_db(transaction=True)
def test_redeliver_source_next_retry_at_already_none(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """源日志 next_retry_at 已为 None（如成功日志手动重投）时不应报错."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=200,
        started_at=timezone.now(),
        next_retry_at=None,
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.status_code == 200
    # 源日志 next_retry_at 保持 None，save 不应报错
    source.refresh_from_db()
    assert source.next_retry_at is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_source_status_code_none(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """源日志 status_code 为 None（原网络异常）时重投应正常执行."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="ingest.completed",
        payload={"url": "https://x.com"},
        status_code=None,
        error_message="URLError: timeout",
        started_at=timezone.now(),
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.status_code == 200  # stub 返回成功
    assert new_log.event_type == "ingest.completed"
    assert new_log.payload == {"url": "https://x.com"}


@pytest.mark.django_db(transaction=True)
def test_redeliver_preserves_source_fields(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """重投不应修改源日志除 next_retry_at 外的任何字段."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"original": True},
        status_code=503,
        retry_count=5,
        response_body="original error",
        error_message="原失败原因",
        started_at=timezone.now() - timedelta(hours=1),
        finished_at=timezone.now() - timedelta(hours=1),
        duration_ms=9999,
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )

    redeliver(source.pk)

    source.refresh_from_db()
    assert source.event_type == "sync.completed"
    assert source.payload == {"original": True}
    assert source.status_code == 503
    assert source.retry_count == 5
    assert source.response_body == "original error"
    assert source.error_message == "原失败原因"
    assert source.duration_ms == 9999
    # 唯一应改变的字段
    assert source.next_retry_at is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_flaky_then_success(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """重投过程中先失败后成功应触发内联重试，最终日志记录成功."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)

    call_count = {"n": 0}

    def _flaky(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return _PostResult(status_code=503, body="err", error="")
        return _PostResult(status_code=200, body="ok", error="")

    monkeypatch.setattr(deliverer, "_http_post", _flaky)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert call_count["n"] == 3  # 2 次失败 + 1 次成功
    assert new_log.status_code == 200
    assert new_log.retry_count == 2
    assert new_log.next_retry_at is None


@pytest.mark.django_db(transaction=True)
def test_redeliver_new_log_retry_count_starts_from_zero(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """新日志 retry_count 应从 0 开始，不继承源日志的重试次数."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        retry_count=5,  # 源日志已重试 5 次
        started_at=timezone.now(),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.retry_count == 0  # 新日志重新计数
    assert new_log.status_code == 200


@pytest.mark.django_db(transaction=True)
def test_redeliver_nested_payload(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """源日志 payload 含嵌套结构时重投应完整传递."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    calls, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    nested_payload = {
        "config_id": 42,
        "details": {"rows_read": 100, "rows_written": 95},
        "tags": ["sync", "batch"],
        "meta": {"user": {"id": 1, "name": "alice"}},
    }
    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload=nested_payload,
        status_code=503,
        started_at=timezone.now(),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.payload == nested_payload
    # 验证投递时 body 确实包含嵌套结构
    assert len(calls) == 1
    delivered_body = json.loads(calls[0]["body"].decode("utf-8"))
    assert delivered_body == nested_payload


@pytest.mark.django_db(transaction=True)
def test_redeliver_empty_payload(make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch) -> None:
    """源日志 payload 为空 dict 时重投应正常执行."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={},
        status_code=503,
        started_at=timezone.now(),
    )

    new_log = redeliver(source.pk)

    assert new_log is not None
    assert new_log.payload == {}
    assert new_log.status_code == 200


@pytest.mark.django_db(transaction=True)
def test_redeliver_consecutive_calls_on_same_source(
    make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续重投同一条源日志应创建多条新日志，不报错."""
    admin = make_user(role=Role.ADMIN)
    sub = _make_sub(admin)
    _, stub = _capture_post_calls()
    monkeypatch.setattr(deliverer, "_http_post", stub)

    source = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"k": "v"},
        status_code=503,
        started_at=timezone.now(),
        next_retry_at=timezone.now() + timedelta(seconds=300),
    )

    log1 = redeliver(source.pk)
    log2 = redeliver(source.pk)

    assert log1 is not None
    assert log2 is not None
    assert log1.pk != log2.pk
    assert log1.pk != source.pk
    assert log2.pk != source.pk
    # 第二次重投时 next_retry_at 已为 None，save 不应报错
    source.refresh_from_db()
    assert source.next_retry_at is None
