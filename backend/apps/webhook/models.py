"""Webhook 订阅与投递日志模型.

``WebhookSubscription`` 记录外部应用订阅的事件配置（URL/密钥/事件类型）；
``WebhookDeliveryLog`` 记录每次投递的请求/响应/重试信息，供排查与重投。

设计要点：

- ``secret`` 以明文存储（与 GitHub/Stripe 等平台一致），由管理面分配并 HTTPS 传输，
  供接收方校验 ``X-Webhook-Signature`` 签名；不进入普通日志。
- ``events`` 为 JSON 数组（如 ``["sync.completed","ingest.completed"]``），
  投递器按事件类型匹配订阅。
- ``signing_algorithm`` 当前固定 ``sha256``（HMAC-SHA256），预留扩展位。
- ``WebhookDeliveryLog.next_retry_at`` 为下一次重试计划时间，便于调度重投
  （本期重试在投递线程内同步指数退避，调度重投为后续扩展点）。
"""

from __future__ import annotations

from django.db import models

from apps.accounts.models import User


class SigningAlgorithm(models.TextChoices):
    """Webhook 签名算法枚举."""

    SHA256 = "sha256", "HMAC-SHA256"


class DeliveryStatus(models.TextChoices):
    """单次投递最终状态枚举（用于统计与展示，非数据库字段）."""

    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"
    PENDING = "pending", "待重试"


class WebhookSubscription(models.Model):
    """Webhook 订阅：外部应用注册的事件接收配置.

    一个订阅对应一个接收端点与一组事件类型；事件触发时投递器按 ``events``
    匹配订阅，对每个匹配订阅起独立线程投递。
    """

    objects: models.Manager[WebhookSubscription]

    name = models.CharField(max_length=128, unique=True, verbose_name="名称")
    url = models.URLField(max_length=512, verbose_name="接收 URL")
    secret = models.CharField(max_length=256, verbose_name="签名密钥")
    signing_algorithm = models.CharField(
        max_length=16,
        choices=SigningAlgorithm.choices,
        default=SigningAlgorithm.SHA256,
        verbose_name="签名算法",
    )
    events = models.JSONField(default=list, blank=True, verbose_name="订阅事件类型列表")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_subscriptions",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "Webhook 订阅"
        verbose_name_plural = "Webhook 订阅"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["is_active"], name="idx_webhook_sub_active"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.name} ({self.url})"  # type: ignore[bad-return]

    def is_subscribed(self, event_type: str) -> bool:
        """判断是否订阅了指定事件类型."""
        return event_type in list(self.events)


class WebhookDeliveryLog(models.Model):
    """Webhook 投递日志：记录每次投递的请求/响应/重试信息."""

    objects: models.Manager[WebhookDeliveryLog]

    subscription = models.ForeignKey(
        WebhookSubscription,
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name="订阅",
    )
    event_type = models.CharField(max_length=64, verbose_name="事件类型")
    payload = models.JSONField(default=dict, verbose_name="投递负载")
    status_code = models.IntegerField(null=True, blank=True, verbose_name="HTTP 响应码")
    retry_count = models.IntegerField(default=0, verbose_name="已重试次数")
    next_retry_at = models.DateTimeField(null=True, blank=True, verbose_name="下次重试时间")
    response_body = models.TextField(blank=True, default="", verbose_name="响应体（截断）")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    started_at = models.DateTimeField(verbose_name="开始投递时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束投递时间")
    duration_ms = models.IntegerField(null=True, blank=True, verbose_name="耗时（毫秒）")

    class Meta:
        verbose_name = "Webhook 投递日志"
        verbose_name_plural = "Webhook 投递日志"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["subscription"], name="idx_webhook_dl_sub"),
            models.Index(fields=["event_type"], name="idx_webhook_dl_event"),
            models.Index(fields=["started_at"], name="idx_webhook_dl_started"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.event_type} -> subscription={self.subscription_id} (status={self.status_code})"  # type: ignore[bad-return]


__all__ = [
    "DeliveryStatus",
    "SigningAlgorithm",
    "WebhookDeliveryLog",
    "WebhookSubscription",
]
