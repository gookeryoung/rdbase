"""Webhook Router — 订阅管理与投递日志查询.

提供：
- GET /webhooks：列出订阅
- POST /webhooks：创建订阅
- GET /webhooks/{id}：获取订阅详情
- PATCH /webhooks/{id}：更新订阅
- DELETE /webhooks/{id}：删除订阅
- GET /webhooks/{id}/deliveries：查询订阅的投递日志

所有端点 ``JWTAuth`` + ``require_admin``：Webhook 配置含签名密钥，仅管理员可访问。
"""

from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.models import User
from apps.accounts.permissions import require_admin
from apps.audit.audit import log_audit
from apps.audit.models import AuditAction

from .models import WebhookDeliveryLog, WebhookSubscription
from .schemas import (
    MessageOut,
    WebhookDeliveryLogListOut,
    WebhookDeliveryLogOut,
    WebhookSubscriptionCreateIn,
    WebhookSubscriptionListOut,
    WebhookSubscriptionOut,
    WebhookSubscriptionUpdateIn,
)

router = Router(tags=["webhooks"], auth=JWTAuth())


def _sub_to_out(sub: WebhookSubscription) -> WebhookSubscriptionOut:
    """将 WebhookSubscription 模型转为 WebhookSubscriptionOut（不回显 secret）."""
    return WebhookSubscriptionOut(
        id=sub.pk,
        name=sub.name,
        url=sub.url,
        signing_algorithm=sub.signing_algorithm,
        events=list(cast("list[str]", sub.events)),
        is_active=sub.is_active,
        created_by_id=sub.created_by_id,
        created_at=sub.created_at,  # type: ignore[missing-attribute]
        updated_at=sub.updated_at,  # type: ignore[missing-attribute]
    )


def _log_to_out(log: WebhookDeliveryLog) -> WebhookDeliveryLogOut:
    """将 WebhookDeliveryLog 模型转为 WebhookDeliveryLogOut."""
    return WebhookDeliveryLogOut(
        id=log.pk,
        subscription_id=log.subscription_id,
        event_type=log.event_type,
        payload=dict(cast("dict[str, Any]", log.payload)),
        status_code=log.status_code,
        retry_count=log.retry_count,
        next_retry_at=log.next_retry_at,
        response_body=log.response_body,
        error_message=log.error_message,
        started_at=log.started_at,  # type: ignore[missing-attribute]
        finished_at=log.finished_at,
        duration_ms=log.duration_ms,
    )


def _get_sub_or_404(sub_id: int) -> WebhookSubscription:
    """按 ID 获取订阅，不存在抛 404."""
    try:
        return WebhookSubscription.objects.get(pk=sub_id)
    except WebhookSubscription.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"Webhook 订阅 {sub_id} 不存在") from None


@router.get("", response={200: WebhookSubscriptionListOut})
def list_subscriptions(request: HttpRequest) -> HttpResponse:
    """列出全部 Webhook 订阅（仅管理员）."""
    require_admin(request)
    qs = WebhookSubscription.objects.all().order_by("-id")
    items = [_sub_to_out(s) for s in qs]
    body = WebhookSubscriptionListOut(items=items, total=len(items)).model_dump(mode="json")
    return JsonResponse(body)


@router.post("", response={201: WebhookSubscriptionOut})
def create_subscription(request: HttpRequest, payload: WebhookSubscriptionCreateIn) -> HttpResponse:
    """创建 Webhook 订阅（仅管理员）."""
    require_admin(request)
    if WebhookSubscription.objects.filter(name=payload.name).exists():
        raise HttpError(400, "订阅名称已存在")
    user = cast(User, getattr(request, "auth", None))
    sub = WebhookSubscription.objects.create(
        name=payload.name,
        url=payload.url,
        secret=payload.secret,
        events=list(payload.events),
        is_active=payload.is_active,
        created_by=user,
    )
    log_audit(
        request,
        action=AuditAction.WEBHOOK_DELIVER,
        resource_type="webhook_subscription",
        resource_id=str(sub.pk),
        extra={"name": sub.name, "url": sub.url, "events": list(payload.events)},
    )
    body = _sub_to_out(sub).model_dump(mode="json")
    return JsonResponse(body, status=201)


@router.get("/{sub_id}", response={200: WebhookSubscriptionOut})
def retrieve_subscription(request: HttpRequest, sub_id: int) -> HttpResponse:
    """获取 Webhook 订阅详情（仅管理员）."""
    require_admin(request)
    sub = _get_sub_or_404(sub_id)
    return JsonResponse(_sub_to_out(sub).model_dump(mode="json"))


@router.patch("/{sub_id}", response={200: WebhookSubscriptionOut})
def update_subscription(
    request: HttpRequest,
    sub_id: int,
    payload: WebhookSubscriptionUpdateIn,
) -> HttpResponse:
    """更新 Webhook 订阅（仅管理员，secret 为空表示不更新）."""
    require_admin(request)
    sub = _get_sub_or_404(sub_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != sub.name and WebhookSubscription.objects.filter(name=data["name"]).exists():
        raise HttpError(400, "订阅名称已存在")

    if "events" in data and data["events"] is not None:
        data["events"] = list(data["events"])
    # secret 为空字符串表示不更新（避免误清空密钥）
    if "secret" in data and not data["secret"]:
        data.pop("secret")

    for field, value in data.items():
        setattr(sub, field, value)
    sub.save()
    log_audit(
        request,
        action=AuditAction.WEBHOOK_DELIVER,
        resource_type="webhook_subscription",
        resource_id=str(sub.pk),
        extra={"updated_fields": list(data.keys())},
    )
    return JsonResponse(_sub_to_out(sub).model_dump(mode="json"))


@router.delete("/{sub_id}", response={200: MessageOut})
def delete_subscription(request: HttpRequest, sub_id: int) -> HttpResponse:
    """删除 Webhook 订阅（仅管理员）."""
    require_admin(request)
    sub = _get_sub_or_404(sub_id)
    sub_name = sub.name
    sub.delete()
    log_audit(
        request,
        action=AuditAction.WEBHOOK_DELIVER,
        resource_type="webhook_subscription",
        resource_id=str(sub_id),
        extra={"name": sub_name, "deleted": True},
    )
    return JsonResponse(MessageOut(detail=f"Webhook 订阅 {sub_name} 已删除").model_dump())


@router.get("/{sub_id}/deliveries", response={200: WebhookDeliveryLogListOut})
def list_deliveries(
    request: HttpRequest,
    sub_id: int,
    event_type: str | None = None,
    limit: int = 50,
) -> HttpResponse:
    """查询指定订阅的投递日志（仅管理员）.

    Query 参数：
        event_type: 按事件类型过滤。
        limit: 返回条数上限，默认 50。
    """
    require_admin(request)
    _get_sub_or_404(sub_id)
    qs = WebhookDeliveryLog.objects.filter(subscription_id=sub_id).order_by("-started_at")
    if event_type:
        qs = qs.filter(event_type=event_type)
    qs = qs[:limit]
    items = [_log_to_out(log) for log in qs]
    total = WebhookDeliveryLog.objects.filter(subscription_id=sub_id).count()
    body = WebhookDeliveryLogListOut(items=items, total=total).model_dump(mode="json")
    return JsonResponse(body)


__all__ = ["router"]
