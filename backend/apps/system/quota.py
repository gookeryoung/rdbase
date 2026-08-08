"""每日写入配额控制.

按 key（通常为 ``dataset_write_daily:{token_prefix}``）维度限制当日写入总行数，
req-03 item 43 数据集写入端点配额。

设计要点：
- Redis 模式：``INCRBY`` 累加当日已用配额，首次自增时 ``EXPIRE`` 到当日结束。
- 本地降级：``threading.Lock`` + ``time.monotonic`` 内存计数，TTL 到当日结束。
- 复用 :func:`apps.system.redis_client.get_redis` 单例。
- ``check_and_consume_quota`` 返回 ``(allowed, remaining)``，超限返回 429。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Protocol

import redis

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 配额键前缀，与限流/幂等/锁的命名空间隔离。
_KEY_PREFIX = "rdbase:quota"


class _QuotaBackend(Protocol):
    """配额后端协议."""

    def check_and_consume(self, key: str, rows: int, daily_limit: int) -> tuple[bool, int]:
        """检查并消费配额.

        Args:
            key: 配额维度 key（不含前缀）。
            rows: 本次请求消耗的行数。
            daily_limit: 每日上限。

        Returns:
            ``(allowed, remaining)``：允许时 remaining 为消费后的剩余配额（>=0）；
            超限时 ``allowed=False``，remaining 为 0。
        """
        ...

    def reset(self, key: str) -> None:
        """清除指定 key 的配额计数（测试用）."""
        ...


def _seconds_to_day_end(now: datetime) -> int:
    """计算从 now 到当日 23:59:59 的剩余秒数（亚洲/Shanghai 本地时间）."""
    end = datetime.combine(now.date(), dtime.max)
    delta = (end - now).total_seconds()
    # 至少保留 1 秒，避免边界 0 TTL。
    return max(1, int(delta))


@dataclass
class _LocalQuotaEntry:
    """本地配额计数项."""

    used: int
    expires_at: float  # monotonic 时间戳


class _LocalBackend:
    """进程内每日配额后端.

    用 ``threading.Lock`` 保护 check-then-act，``time.monotonic`` 计时。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _LocalQuotaEntry] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def check_and_consume(self, key: str, rows: int, daily_limit: int) -> tuple[bool, int]:
        with self._lock:
            now = self._now()
            entry = self._entries.get(key)
            if entry is not None and now < entry.expires_at:
                if entry.used + rows > daily_limit:
                    return False, 0
                entry.used += rows
                return True, max(0, daily_limit - entry.used)
            # 新的一天：重置计数，TTL 到当日结束（本地用单调时钟近似）
            ttl = _seconds_to_day_end(datetime.now())
            self._entries[key] = _LocalQuotaEntry(used=rows, expires_at=now + ttl)
            return True, max(0, daily_limit - rows)

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class _RedisBackend:
    """Redis 每日配额后端.

    ``INCRBY`` 原子累加，首次累加（返回值 == rows）时 ``EXPIRE`` 到当日结束。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, key: str) -> str:
        return f"{_KEY_PREFIX}:{key}"

    def check_and_consume(self, key: str, rows: int, daily_limit: int) -> tuple[bool, int]:
        redis_key = self._k(key)
        ttl = _seconds_to_day_end(datetime.now())
        pipe = self._client.pipeline()
        try:
            pipe.incrby(redis_key, rows)
            pipe.expire(redis_key, ttl, nx=True)
            used, _ = pipe.execute()
        except redis.RedisError as exc:
            # Redis 故障时降级放行，避免配额故障导致服务不可用。
            logger.warning("Redis 配额计数失败 key=%s: %s，临时放行", key, exc)
            return True, max(0, daily_limit - rows)
        used_int = int(used)
        if used_int > daily_limit:
            return False, 0
        return True, max(0, daily_limit - used_int)

    def reset(self, key: str) -> None:
        try:
            self._client.delete(self._k(key))
        except redis.RedisError:
            logger.debug("清除配额计数失败 key=%s", key, exc_info=True)


# 模块级后端单例，用锁保护 check-then-act。
_backend: _QuotaBackend | None = None
_backend_lock = threading.Lock()


def _resolve_backend() -> _QuotaBackend:
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
            logger.info("配额后端：Redis 共享（多 worker 跨进程生效）")
        else:
            _backend = _LocalBackend()
            logger.warning("Redis 未配置，配额降级为本地内存（仅本进程生效）")
        return _backend


def reset_quota() -> None:
    """重置后端单例（仅测试用）."""
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _backend_lock:
        _backend = None


def check_and_consume_quota(key: str, rows: int, daily_limit: int) -> tuple[bool, int]:
    """检查并消费每日写入配额.

    Args:
        key: 配额维度 key（如 ``dataset_write_daily:{token_prefix}``）。
        rows: 本次请求消耗的行数。
        daily_limit: 每日写入行数上限。

    Returns:
        ``(allowed, remaining)``：允许时 ``allowed=True, remaining>=0``；
        超限时 ``allowed=False, remaining=0``。
    """
    return _resolve_backend().check_and_consume(key, rows, daily_limit)


def reset_quota_key(key: str) -> None:
    """清除指定 key 的配额计数（测试用）."""
    _resolve_backend().reset(key)


__all__ = [
    "check_and_consume_quota",
    "reset_quota",
    "reset_quota_key",
]
