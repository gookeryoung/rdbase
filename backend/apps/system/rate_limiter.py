"""固定窗口速率限制器.

按 key 维度限制单位时间内的请求数，req-03 item 43 数据集写入端点限流。

设计要点：
- Redis 模式：``INCR key`` + 首次 ``EXPIRE``，原子计数 + 窗口自动过期。
- 本地降级：``threading.Lock`` + ``time.monotonic`` 内存窗口，单进程生效。
- 复用 :func:`apps.system.redis_client.get_redis` 单例；Redis 不可用时降级本地。
- ``check_rate_limit`` 返回 ``(allowed, retry_after_seconds)``，超限时 retry_after
  提示客户端稍后重试（HTTP 429）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import redis

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 限流键前缀，与幂等/锁的命名空间隔离。
_KEY_PREFIX = "rdbase:rate"


class _RateBackend(Protocol):
    """限流后端协议."""

    def check(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """检查并计数.

        Args:
            key: 限流 key（不含前缀）。
            max_requests: 窗口内允许的最大请求数。
            window_seconds: 窗口时长（秒）。

        Returns:
            ``(allowed, retry_after_seconds)``：允许时 retry_after=0；
            超限时 retry_after 为窗口剩余秒数（至少 1）。
        """
        ...

    def reset(self, key: str) -> None:
        """清除指定 key 的计数（测试用）."""
        ...


@dataclass
class _LocalEntry:
    """本地窗口计数项."""

    count: int
    expires_at: float  # monotonic 时间戳


class _LocalBackend:
    """进程内固定窗口计数后端.

    用 ``threading.Lock`` 保护 check-then-act，``time.monotonic`` 计时。
    仅本进程生效，适用于 Redis 不可用时的降级场景。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _LocalEntry] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def check(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        with self._lock:
            now = self._now()
            entry = self._entries.get(key)
            if entry is not None and now < entry.expires_at:
                if entry.count >= max_requests:
                    retry_after = max(1, int(entry.expires_at - now))
                    return False, retry_after
                entry.count += 1
                return True, 0
            # 新窗口
            self._entries[key] = _LocalEntry(count=1, expires_at=now + window_seconds)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class _RedisBackend:
    """Redis 固定窗口计数后端.

    ``INCR`` 原子自增，首次自增（返回 1）时 ``EXPIRE`` 设置窗口 TTL。
    跨进程共享计数，适用于多 worker 部署。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, key: str) -> str:
        return f"{_KEY_PREFIX}:{key}"

    def check(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        redis_key = self._k(key)
        # pipeline 保证 INCR 与 EXPIRE 原子提交（窗口内首次才设 EXPIRE）。
        pipe = self._client.pipeline()
        try:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds, nx=True)
            count, _ = pipe.execute()
        except redis.RedisError as exc:
            # Redis 故障时降级放行，避免限流器故障导致服务不可用。
            logger.warning("Redis 限流计数失败 key=%s: %s，临时放行", key, exc)
            return True, 0
        count_int = int(count)
        if count_int > max_requests:
            ttl = self._client.ttl(redis_key)
            retry_after = max(1, int(ttl)) if ttl and ttl > 0 else 1
            return False, retry_after
        return True, 0

    def reset(self, key: str) -> None:
        try:
            self._client.delete(self._k(key))
        except redis.RedisError:
            logger.debug("清除限流计数失败 key=%s", key, exc_info=True)


# 模块级后端单例，用锁保护 check-then-act。
_backend: _RateBackend | None = None
_backend_lock = threading.Lock()


def _resolve_backend() -> _RateBackend:
    """解析后端：Redis 可用时用共享后端，否则本地内存."""
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        client = get_redis()
        if client is not None:
            _backend = _RedisBackend(client)
            logger.info("限流后端：Redis 共享（多 worker 跨进程生效）")
        else:
            _backend = _LocalBackend()
            logger.warning("Redis 未配置，限流降级为本地内存（仅本进程生效）")
        return _backend


def reset_rate_limiter() -> None:
    """重置后端单例（仅测试用）.

    清空缓存的后端实例，下次调用重新按 settings 解析后端。
    """
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _backend_lock:
        _backend = None


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    """检查速率限制并计数.

    Args:
        key: 限流维度 key（如 ``dataset_write:{token_prefix}``）。
        max_requests: 窗口内允许的最大请求数。
        window_seconds: 窗口时长（秒）。

    Returns:
        ``(allowed, retry_after_seconds)``：允许时 ``allowed=True, retry_after=0``；
        超限时 ``allowed=False, retry_after>=1``（窗口剩余秒数，供 429 响应使用）。
    """
    return _resolve_backend().check(key, max_requests, window_seconds)


def reset_rate_key(key: str) -> None:
    """清除指定 key 的计数（测试用）."""
    _resolve_backend().reset(key)


__all__ = [
    "check_rate_limit",
    "reset_rate_key",
    "reset_rate_limiter",
]
