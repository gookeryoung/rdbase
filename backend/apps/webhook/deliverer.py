"""Webhook 事件投递器.

入口 :func:`deliver_event` 由业务层（``SyncService``/``execute_task`` 等）在事件
完成时调用，按订阅 ``events`` 列表匹配活跃订阅，对每个匹配订阅起独立后台线程
执行投递（含指数退避重试）。

设计要点：

- **签名**：HMAC-SHA256，请求头 ``X-Webhook-Signature: sha256=<hex>``，接收方按
  同样算法用 ``subscription.secret`` 校验完整性。
- **重试**：指数退避 1/2/4/8/16s，最多 5 次重试（总尝试 6 次）。2xx 视为成功
  不再重试；其余状态码或网络异常触发重试。内联重试全部失败后，日志
  ``next_retry_at`` 设为 ``now + _SCHEDULED_RETRY_INTERVAL``，标记待调度重投。
- **后台线程**：每个订阅独立线程，主调用方不阻塞；线程内 finally 关闭 Django
  DB 连接，避免线程池复用泄漏。
- **日志**：每次投递流程（含全部重试）写一条 :class:`WebhookDeliveryLog`，
  ``retry_count`` 记录最终重试次数；``next_retry_at`` 非 None 表示待调度重投。
- **重投**：:func:`redeliver` 按源日志 ID 重新投递（手动/调度），创建新日志，
  原日志保留作审计；调用前清源日志 ``next_retry_at`` 避免重复调度。
- **可测性**：模块级 ``_backoff_sleep = time.sleep``，测试可 monkeypatch 为空操作
  避免真实等待；``_http_post`` 钩子同样可替换为 stub。

使用标准库 ``urllib`` 实现投递，避免引入新依赖（项目未依赖 ``requests``）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from django.db import connections
from django.utils import timezone

from .models import WebhookDeliveryLog, WebhookSubscription

logger = logging.getLogger(__name__)

# 指数退避序列：第 i 次重试前 sleep delays[i-1] 秒（i 从 1 起）。
_BACKOFF_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
# 最大重试次数（不含首次尝试）。
_MAX_RETRIES = 5
# 单次请求超时秒数。
_REQUEST_TIMEOUT = 10
# 响应体最大记录长度（防日志膨胀）。
_MAX_BODY_LOG = 4096
# 调度重投间隔：内联重试全部失败后，下次调度重投的等待时间（秒）。
_SCHEDULED_RETRY_INTERVAL = 300

# 退避 sleep 钩子：默认 time.sleep，测试可 monkeypatch 为空操作。
_backoff_sleep: Callable[[float], None] = time.sleep


class _PostResult:
    """单次 HTTP POST 结果（内部结构）."""

    __slots__ = ("body", "error", "status_code")

    def __init__(self, status_code: int | None, body: str, error: str) -> None:
        self.status_code = status_code
        self.body = body
        self.error = error


def _http_post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> _PostResult:
    """同步 POST 一个请求，返回结果对象.

    用 ``urllib.request`` 实现；HTTPError（非 2xx）也返回结果（含状态码），
    URLError（网络层异常）返回 ``status_code=None`` 与 ``error`` 描述。
    """
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # HTTP 错误状态码：仍属"收到响应"，但非 2xx，需重试。
        status = exc.code
        try:
            raw = exc.read()
        except Exception:
            raw = b""
    except urllib.error.URLError as exc:
        return _PostResult(status_code=None, body="", error=f"URLError: {exc.reason}")
    except (OSError, TimeoutError) as exc:
        return _PostResult(status_code=None, body="", error=f"{type(exc).__name__}: {exc}")

    text = raw.decode("utf-8", errors="replace")[:_MAX_BODY_LOG]
    return _PostResult(status_code=status, body=text, error="")


def _is_success(status_code: int | None) -> bool:
    """判断状态码是否为成功（2xx）."""
    return status_code is not None and 200 <= status_code < 300


def deliver_event(event_type: str, payload: dict[str, Any], *, wait: bool = False) -> None:
    """事件分发入口：匹配订阅并对每个起后台线程投递.

    Args:
        event_type: 事件类型（如 ``"sync.completed"``/``"ingest.completed"``）。
        payload: 投递负载（JSON 序列化后作为请求体）。
        wait: True 时同步等待所有投递线程完成再返回（供子进程场景，如
            ``execute_task`` 在 ``spawn_ingest`` 子进程内运行，进程退出会杀
            daemon 线程，故需等待）；默认 False，主请求线程不阻塞。
    """
    subs = WebhookSubscription.objects.filter(is_active=True)
    matched_ids = [s.pk for s in subs if event_type in list(s.events)]
    if not matched_ids:
        logger.info("Webhook 事件分发: event=%s 无匹配订阅，跳过", event_type)
        return

    logger.info(
        "Webhook 事件分发: event=%s 匹配 %d 个订阅 ids=%s wait=%s",
        event_type,
        len(matched_ids),
        matched_ids,
        wait,
    )
    threads: list[threading.Thread] = []
    for sub_id in matched_ids:
        thread = threading.Thread(
            target=_deliver_one,
            args=(sub_id, event_type, payload),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    if wait:
        for t in threads:
            t.join()


def _deliver_one(subscription_id: int, event_type: str, payload: dict[str, Any]) -> WebhookDeliveryLog | None:
    """单订阅投递：含指数退避重试，最终写一条 DeliveryLog 并返回.

    线程入口函数，不向上抛异常（所有异常被捕获并记录到日志字段）。
    投递失败（status 非 2xx）时日志 ``next_retry_at`` 设为
    ``now + _SCHEDULED_RETRY_INTERVAL``，标记待调度重投；成功时保持 None。

    Returns:
        创建的 :class:`WebhookDeliveryLog`；订阅不存在或日志写入失败时返回 None。
    """
    started_at = timezone.now()
    last_status: int | None = None
    last_body = ""
    last_error = ""
    retry_count = 0
    created_log: WebhookDeliveryLog | None = None

    logger.info(
        "Webhook 投递开始: subscription_id=%s event=%s",
        subscription_id,
        event_type,
    )
    try:
        try:
            sub = WebhookSubscription.objects.get(pk=subscription_id)
        except WebhookSubscription.DoesNotExist:  # type: ignore[missing-attribute]
            logger.warning("Webhook 订阅 %s 不存在，跳过投递", subscription_id)
            return None

        logger.info(
            "Webhook 投递就绪: subscription=%s(%s) event=%s url=%s",
            sub.name,
            subscription_id,
            event_type,
            sub.url,
        )
        body_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        signature = hmac.new(
            sub.secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Event": event_type,
        }

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                logger.info(
                    "Webhook 退避重试 %d/%d: subscription=%s event=%s delay=%.1fs",
                    attempt,
                    _MAX_RETRIES,
                    sub.name,
                    event_type,
                    _BACKOFF_DELAYS[attempt - 1],
                )
                _backoff_sleep(_BACKOFF_DELAYS[attempt - 1])
                retry_count = attempt
            result = _http_post(sub.url, body_bytes, headers, _REQUEST_TIMEOUT)
            last_status = result.status_code
            last_body = result.body
            last_error = result.error
            logger.info(
                "Webhook 投递响应: subscription=%s event=%s attempt=%d/%d status=%s error=%s",
                sub.name,
                event_type,
                attempt + 1,
                _MAX_RETRIES + 1,
                last_status,
                last_error or "无",
            )
            if _is_success(last_status):
                break

        if not _is_success(last_status):
            logger.warning(
                "Webhook 投递失败: subscription=%s event=%s status=%s retries=%d",
                sub.name,
                event_type,
                last_status,
                retry_count,
            )
    except Exception:
        logger.exception("Webhook 投递异常: subscription_id=%s event=%s", subscription_id, event_type)
        last_error = last_error or "投递过程异常"
    finally:
        finished_at = timezone.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        try:
            next_retry_at: datetime | None = None
            if not _is_success(last_status):
                next_retry_at = timezone.now() + timedelta(seconds=_SCHEDULED_RETRY_INTERVAL)
                logger.info(
                    "Webhook 标记待调度重投: subscription_id=%s event=%s next_retry_at=%s",
                    subscription_id,
                    event_type,
                    next_retry_at.isoformat(),
                )
            created_log = WebhookDeliveryLog.objects.create(
                subscription_id=subscription_id,
                event_type=event_type,
                payload=payload,
                status_code=last_status,
                retry_count=retry_count,
                next_retry_at=next_retry_at,
                response_body=last_body,
                error_message=last_error,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
            logger.info(
                "Webhook DeliveryLog 已写入: log_id=%s subscription_id=%s event=%s status=%s retries=%d duration_ms=%d",
                created_log.pk,
                subscription_id,
                event_type,
                last_status,
                retry_count,
                duration_ms,
            )
        except Exception:
            logger.exception("Webhook DeliveryLog 写入失败: subscription_id=%s", subscription_id)
        finally:
            connections.close_all()
    return created_log


def redeliver(log_id: int) -> WebhookDeliveryLog | None:
    """按源日志 ID 重新投递（手动/调度入口）.

    读源日志 → 清其 ``next_retry_at``（避免重复调度）→ 在独立线程同步调
    :func:`_deliver_one` 用原始 ``event_type`` + ``payload`` 投递 → 返回新日志。
    原日志保留作审计，不修改除 ``next_retry_at`` 外的字段。

    在独立线程执行投递（阻塞等待完成），避免 ``_deliver_one`` 的
    ``connections.close_all()`` 关闭调用方线程（API 请求/管理命令）的 DB 连接。

    Args:
        log_id: 源 :class:`WebhookDeliveryLog` 主键。

    Returns:
        新创建的 :class:`WebhookDeliveryLog`；源日志不存在或订阅不存在时返回 None。
    """
    try:
        source = WebhookDeliveryLog.objects.get(pk=log_id)
    except WebhookDeliveryLog.DoesNotExist:  # type: ignore[missing-attribute]
        logger.warning("Webhook 重投：源日志 %s 不存在", log_id)
        return None

    logger.info(
        "Webhook 重投开始: source_log_id=%s subscription_id=%s event=%s status=%s next_retry_at=%s",
        log_id,
        source.subscription_id,
        source.event_type,
        source.status_code,
        source.next_retry_at,
    )

    # 清除源日志的 next_retry_at，避免调度器重复重投
    if source.next_retry_at is not None:
        source.next_retry_at = None
        source.save(update_fields=["next_retry_at"])
        logger.info("Webhook 重投：已清源日志 %s 的 next_retry_at", log_id)

    sub_id = source.subscription_id
    event_type = source.event_type
    payload_copy = dict(source.payload)
    result: list[WebhookDeliveryLog | None] = [None]

    def _run() -> None:
        result[0] = _deliver_one(sub_id, event_type, payload_copy)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join()

    new_log = result[0]
    if new_log is not None:
        logger.info(
            "Webhook 重投完成: source_log_id=%s new_log_id=%s status=%s next_retry_at=%s",
            log_id,
            new_log.pk,
            new_log.status_code,
            new_log.next_retry_at,
        )
    else:
        logger.warning(
            "Webhook 重投失败: source_log_id=%s 未创建新日志（订阅可能已删除）",
            log_id,
        )
    return new_log


__all__ = ["deliver_event", "redeliver"]
