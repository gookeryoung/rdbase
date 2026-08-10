"""Webhook 订阅管理与投递日志查询 API 测试."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.webhook import deliverer
from apps.webhook.deliverer import _PostResult
from apps.webhook.models import WebhookDeliveryLog, WebhookSubscription
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _post(client: Client, url: str, body: dict[str, Any], h: dict[str, str]) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _patch(client: Client, url: str, body: dict[str, Any], h: dict[str, str]) -> HttpResponse:
    return cast(
        HttpResponse,
        client.patch(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _make_sub(  # noqa: PLR0913
    user: User,
    *,
    name: str = "api-sub",
    url: str = "https://example.com/hook",
    secret: str = "secret-x",
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


class TestWebhookSubscriptionCRUD:
    """订阅 CRUD 与权限测试."""

    def test_list_requires_admin(self, client: Client, regular_user: User) -> None:
        """非管理员访问应被拒绝."""
        resp = client.get("/api/v1/webhooks", **_auth(regular_user))
        assert resp.status_code in {401, 403}

    def test_list_unauthenticated(self, client: Client, db: Any) -> None:
        """未认证应返回 401."""
        resp = client.get("/api/v1/webhooks")
        assert resp.status_code == 401

    def test_list_empty(self, client: Client, admin_user: User) -> None:
        """无订阅时应返回空列表."""
        resp = client.get("/api/v1/webhooks", **_auth(admin_user))
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    def test_create_success(self, client: Client, admin_user: User) -> None:
        """管理员应能创建订阅."""
        body = {
            "name": "new-sub",
            "url": "https://example.com/h",
            "secret": "s3cret",
            "events": ["sync.completed", "ingest.completed"],
        }
        resp = _post(client, "/api/v1/webhooks", body, _auth(admin_user))
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-sub"
        assert data["url"] == "https://example.com/h"
        assert data["events"] == ["sync.completed", "ingest.completed"]
        assert data["is_active"] is True
        assert data["signing_algorithm"] == "sha256"
        assert "secret" not in data
        assert data["created_by_id"] == admin_user.pk

    def test_create_duplicate_name(self, client: Client, admin_user: User) -> None:
        """重名应返回 400."""
        _make_sub(admin_user, name="dup")
        body = {"name": "dup", "url": "https://x.com/h", "secret": "s"}
        resp = _post(client, "/api/v1/webhooks", body, _auth(admin_user))
        assert resp.status_code == 400

    def test_create_viewer_forbidden(self, client: Client, regular_user: User) -> None:
        """非管理员创建应被拒绝."""
        body = {"name": "x", "url": "https://x.com/h", "secret": "s"}
        resp = _post(client, "/api/v1/webhooks", body, _auth(regular_user))
        assert resp.status_code in {401, 403}

    def test_retrieve(self, client: Client, admin_user: User) -> None:
        """管理员应能获取订阅详情."""
        sub = _make_sub(admin_user, name="get-sub")
        resp = client.get(f"/api/v1/webhooks/{sub.pk}", **_auth(admin_user))
        assert resp.status_code == 200
        assert resp.json()["id"] == sub.pk

    def test_retrieve_not_found(self, client: Client, admin_user: User) -> None:
        """获取不存在订阅应返回 404."""
        resp = client.get("/api/v1/webhooks/99999", **_auth(admin_user))
        assert resp.status_code == 404

    def test_update_fields(self, client: Client, admin_user: User) -> None:
        """应能更新订阅字段."""
        sub = _make_sub(admin_user, name="upd-sub")
        resp = _patch(
            client,
            f"/api/v1/webhooks/{sub.pk}",
            {"url": "https://new.com/h", "events": ["ingest.completed"]},
            _auth(admin_user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://new.com/h"
        assert data["events"] == ["ingest.completed"]
        sub.refresh_from_db()
        assert sub.url == "https://new.com/h"
        # secret 未传不应被清空
        assert sub.secret == "secret-x"

    def test_update_secret_keeps_when_empty(self, client: Client, admin_user: User) -> None:
        """secret 传空字符串应不更新."""
        sub = _make_sub(admin_user, name="upd-sec", secret="orig")
        resp = _patch(
            client,
            f"/api/v1/webhooks/{sub.pk}",
            {"secret": ""},
            _auth(admin_user),
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.secret == "orig"

    def test_update_duplicate_name(self, client: Client, admin_user: User) -> None:
        """更新为已存在名称应返回 400."""
        _make_sub(admin_user, name="exist-name")
        sub2 = _make_sub(admin_user, name="upd-dup")
        resp = _patch(
            client,
            f"/api/v1/webhooks/{sub2.pk}",
            {"name": "exist-name"},
            _auth(admin_user),
        )
        assert resp.status_code == 400

    def test_update_not_found(self, client: Client, admin_user: User) -> None:
        """更新不存在订阅应返回 404."""
        resp = _patch(
            client,
            "/api/v1/webhooks/99999",
            {"url": "https://x.com"},
            _auth(admin_user),
        )
        assert resp.status_code == 404

    def test_delete(self, client: Client, admin_user: User) -> None:
        """管理员应能删除订阅."""
        sub = _make_sub(admin_user, name="del-sub")
        resp = client.delete(f"/api/v1/webhooks/{sub.pk}", **_auth(admin_user))
        assert resp.status_code == 200
        assert not WebhookSubscription.objects.filter(pk=sub.pk).exists()

    def test_delete_not_found(self, client: Client, admin_user: User) -> None:
        """删除不存在订阅应返回 404."""
        resp = client.delete("/api/v1/webhooks/99999", **_auth(admin_user))
        assert resp.status_code == 404


class TestWebhookDeliveryLogAPI:
    """投递日志查询测试."""

    def test_list_deliveries_empty(self, client: Client, admin_user: User) -> None:
        """无日志时应返回空列表."""
        sub = _make_sub(admin_user, name="dl-empty")
        resp = client.get(f"/api/v1/webhooks/{sub.pk}/deliveries", **_auth(admin_user))
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    def test_list_deliveries_with_logs(self, client: Client, admin_user: User) -> None:
        """应能列出指定订阅的投递日志."""
        sub = _make_sub(admin_user, name="dl-logs")
        for i in range(3):
            WebhookDeliveryLog.objects.create(
                subscription=sub,
                event_type="sync.completed",
                payload={"i": i},
                status_code=200,
                started_at=timezone.now(),
            )
        resp = client.get(f"/api/v1/webhooks/{sub.pk}/deliveries", **_auth(admin_user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_list_deliveries_filter_event_type(self, client: Client, admin_user: User) -> None:
        """应支持按 event_type 过滤."""
        sub = _make_sub(admin_user, name="dl-filter")
        WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="sync.completed",
            payload={},
            status_code=200,
            started_at=timezone.now(),
        )
        WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="ingest.completed",
            payload={},
            status_code=200,
            started_at=timezone.now(),
        )
        resp = client.get(
            f"/api/v1/webhooks/{sub.pk}/deliveries?event_type=ingest.completed",
            **_auth(admin_user),
        )
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["event_type"] == "ingest.completed"

    def test_list_deliveries_limit(self, client: Client, admin_user: User) -> None:
        """limit 应限制返回条数."""
        sub = _make_sub(admin_user, name="dl-limit")
        for _ in range(5):
            WebhookDeliveryLog.objects.create(
                subscription=sub,
                event_type="sync.completed",
                payload={},
                status_code=200,
                started_at=timezone.now(),
            )
        resp = client.get(
            f"/api/v1/webhooks/{sub.pk}/deliveries?limit=2",
            **_auth(admin_user),
        )
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5

    def test_list_deliveries_subscription_not_found(self, client: Client, admin_user: User) -> None:
        """订阅不存在应返回 404."""
        resp = client.get("/api/v1/webhooks/99999/deliveries", **_auth(admin_user))
        assert resp.status_code == 404

    def test_list_deliveries_requires_admin(self, client: Client, regular_user: User, admin_user: User) -> None:
        """非管理员访问应被拒绝."""
        sub = _make_sub(admin_user, name="dl-perm")
        resp = client.get(
            f"/api/v1/webhooks/{sub.pk}/deliveries",
            **_auth(regular_user),
        )
        assert resp.status_code in {401, 403}


def _stub_post_success() -> tuple[list[dict[str, Any]], Any]:
    """构造始终返回 200 的 _http_post 替身."""
    calls: list[dict[str, Any]] = []

    def _stub(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        calls.append({"url": url, "body": body, "headers": dict(headers), "timeout": timeout})
        return _PostResult(status_code=200, body="ok", error="")

    return calls, _stub


def _stub_post_fail() -> Any:
    """构造始终返回 500 的 _http_post 替身."""

    def _stub(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
        return _PostResult(status_code=500, body="err", error="")

    return _stub


def _noop_sleep(_delay: float) -> None:
    """空操作 sleep 替身."""
    return None


class TestWebhookRedeliverAPI:
    """重投端点测试."""

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_success(self, client: Client, admin_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
        """管理员应能重投指定日志，返回新日志."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success()[1])
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        sub = _make_sub(admin_user, name="rdeliver-ok")
        source = WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="sync.completed",
            payload={"config_id": 1},
            status_code=503,
            started_at=timezone.now(),
            next_retry_at=timezone.now() + timedelta(seconds=300),
        )

        resp = client.post(
            f"/api/v1/webhooks/{sub.pk}/deliveries/{source.pk}/redeliver",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] != source.pk
        assert data["subscription_id"] == sub.pk
        assert data["event_type"] == "sync.completed"
        assert data["payload"] == {"config_id": 1}
        assert data["status_code"] == 200
        assert data["next_retry_at"] is None

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_subscription_not_found(self, client: Client, admin_user: User) -> None:
        """订阅不存在应返回 404."""
        resp = client.post(
            "/api/v1/webhooks/99999/deliveries/1/redeliver",
            **_auth(admin_user),
        )
        assert resp.status_code == 404

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_log_not_found(self, client: Client, admin_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
        """日志不存在应返回 404."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success()[1])
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        sub = _make_sub(admin_user, name="rdeliver-nolog")

        resp = client.post(
            f"/api/v1/webhooks/{sub.pk}/deliveries/99999/redeliver",
            **_auth(admin_user),
        )
        assert resp.status_code == 404

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_log_belongs_to_other_sub(
        self, client: Client, admin_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """日志不属于该订阅应返回 404."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success()[1])
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        sub_a = _make_sub(admin_user, name="sub-a")
        sub_b = _make_sub(admin_user, name="sub-b", url="https://other.com/h")
        source = WebhookDeliveryLog.objects.create(
            subscription=sub_a,
            event_type="sync.completed",
            payload={},
            status_code=503,
            started_at=timezone.now(),
        )

        # 用 sub_b 的 ID 去重投 sub_a 的日志
        resp = client.post(
            f"/api/v1/webhooks/{sub_b.pk}/deliveries/{source.pk}/redeliver",
            **_auth(admin_user),
        )
        assert resp.status_code == 404

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_requires_admin(
        self, client: Client, regular_user: User, admin_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非管理员重投应被拒绝."""
        monkeypatch.setattr(deliverer, "_http_post", _stub_post_success()[1])
        monkeypatch.setattr(deliverer, "_backoff_sleep", _noop_sleep)
        sub = _make_sub(admin_user, name="rdeliver-perm")
        source = WebhookDeliveryLog.objects.create(
            subscription=sub,
            event_type="sync.completed",
            payload={},
            status_code=503,
            started_at=timezone.now(),
        )

        resp = client.post(
            f"/api/v1/webhooks/{sub.pk}/deliveries/{source.pk}/redeliver",
            **_auth(regular_user),
        )
        assert resp.status_code in {401, 403}

    @pytest.mark.django_db(transaction=True)
    def test_redeliver_unauthenticated(self, client: Client, admin_user: User) -> None:
        """未认证应返回 401."""
        sub = _make_sub(admin_user, name="rdeliver-noauth")
        resp = client.post(
            f"/api/v1/webhooks/{sub.pk}/deliveries/1/redeliver",
        )
        assert resp.status_code == 401
