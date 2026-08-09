"""Webhook 订阅与投递日志模型测试."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.models import Role, User
from apps.webhook.models import (
    DeliveryStatus,
    SigningAlgorithm,
    WebhookDeliveryLog,
    WebhookSubscription,
)
from django.utils import timezone

# ---------- 枚举（无需 DB） ----------


def test_signing_algorithm_choices() -> None:
    """SigningAlgorithm 枚举应有 sha256 一项."""
    assert SigningAlgorithm.SHA256 == "sha256"
    assert len(SigningAlgorithm.choices) == 1


def test_delivery_status_choices() -> None:
    """DeliveryStatus 枚举应有 success/failed/pending 三项."""
    assert DeliveryStatus.SUCCESS == "success"
    assert DeliveryStatus.FAILED == "failed"
    assert DeliveryStatus.PENDING == "pending"
    assert len(DeliveryStatus.choices) == 3


# ---------- 模型实例与默认值（需 DB） ----------


@pytest.mark.django_db
def test_subscription_defaults(make_user: Callable[..., User]) -> None:
    """WebhookSubscription 默认值：signing_algorithm=sha256, is_active=True, events=[]."""
    user = make_user(role=Role.ADMIN)
    sub = WebhookSubscription.objects.create(
        name="sub-defaults",
        url="https://example.com/hook",
        secret="s3cret",
        created_by=user,
    )
    assert sub.signing_algorithm == SigningAlgorithm.SHA256
    assert sub.is_active is True
    assert sub.events == []
    assert sub.created_by_id == user.pk
    assert sub.created_at is not None
    assert sub.updated_at is not None


@pytest.mark.django_db
def test_subscription_str_representation() -> None:
    """__str__ 应为 'name (url)' 格式."""
    sub = WebhookSubscription.objects.create(
        name="sub-str",
        url="https://example.com/h",
        secret="x",
    )
    assert str(sub) == "sub-str (https://example.com/h)"


@pytest.mark.django_db
def test_subscription_is_subscribed() -> None:
    """is_subscribed 应按 events 列表匹配."""
    sub = WebhookSubscription.objects.create(
        name="sub-events",
        url="https://example.com/h",
        secret="x",
        events=["sync.completed", "ingest.completed"],
    )
    assert sub.is_subscribed("sync.completed") is True
    assert sub.is_subscribed("ingest.completed") is True
    assert sub.is_subscribed("other.event") is False


@pytest.mark.django_db
def test_subscription_unique_name() -> None:
    """订阅名称应唯一."""
    WebhookSubscription.objects.create(name="uniq", url="https://a.com", secret="x")
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        WebhookSubscription.objects.create(name="uniq", url="https://b.com", secret="y")


@pytest.mark.django_db
def test_subscription_meta_indexes() -> None:
    """Meta.indexes 应包含 idx_webhook_sub_active."""
    index_names = {idx.name for idx in WebhookSubscription._meta.indexes}  # type: ignore[missing-attribute]
    assert "idx_webhook_sub_active" in index_names


@pytest.mark.django_db
def test_subscription_cascade_set_null_on_user_delete(
    make_user: Callable[..., User],
) -> None:
    """user 删除时 created_by 应置 NULL."""
    user = make_user(username="wh-user", role=Role.ADMIN)
    sub = WebhookSubscription.objects.create(
        name="sub-cascade",
        url="https://example.com/h",
        secret="x",
        created_by=user,
    )
    user.delete()
    sub.refresh_from_db()
    assert sub.created_by_id is None


# ---------- DeliveryLog ----------


@pytest.mark.django_db
def test_delivery_log_creation() -> None:
    """DeliveryLog 应能记录完整投递信息."""
    sub = WebhookSubscription.objects.create(
        name="sub-dl",
        url="https://example.com/h",
        secret="x",
        events=["sync.completed"],
    )
    started = timezone.now()
    log = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        payload={"config_id": 1, "status": "success"},
        status_code=200,
        retry_count=0,
        response_body='{"ok":true}',
        started_at=started,
        finished_at=timezone.now(),
        duration_ms=42,
    )
    assert log.subscription_id == sub.pk
    assert log.event_type == "sync.completed"
    assert log.status_code == 200
    assert log.retry_count == 0
    assert log.duration_ms == 42
    assert log.error_message == ""
    assert log.next_retry_at is None


@pytest.mark.django_db
def test_delivery_log_default_retry_count() -> None:
    """retry_count 默认为 0."""
    sub = WebhookSubscription.objects.create(
        name="sub-dl-default",
        url="https://example.com/h",
        secret="x",
    )
    log = WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        started_at=timezone.now(),
    )
    assert log.retry_count == 0
    assert log.status_code is None
    assert log.duration_ms is None


@pytest.mark.django_db
def test_delivery_log_cascade_on_subscription_delete() -> None:
    """订阅删除时关联 DeliveryLog 应级联删除."""
    sub = WebhookSubscription.objects.create(
        name="sub-cascade-dl",
        url="https://example.com/h",
        secret="x",
    )
    WebhookDeliveryLog.objects.create(
        subscription=sub,
        event_type="sync.completed",
        started_at=timezone.now(),
    )
    assert WebhookDeliveryLog.objects.filter(subscription=sub).count() == 1
    sub_pk = sub.pk
    sub.delete()
    assert WebhookDeliveryLog.objects.filter(subscription_id=sub_pk).count() == 0


@pytest.mark.django_db
def test_delivery_log_meta_indexes() -> None:
    """DeliveryLog.Meta.indexes 应包含 sub/event/started 三个索引."""
    index_names = {idx.name for idx in WebhookDeliveryLog._meta.indexes}  # type: ignore[missing-attribute]
    assert index_names == {
        "idx_webhook_dl_sub",
        "idx_webhook_dl_event",
        "idx_webhook_dl_started",
    }
