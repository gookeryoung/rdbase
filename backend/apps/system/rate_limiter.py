"""令牌桶速率限制器.

按 key 维度限制请求速率，采用令牌桶算法（req-03 item 45 升级）：

- **容量 capacity**：桶最大令牌数（允许的突发上限）。
- **补充速率 refill_rate_per_sec**：每秒补充的令牌数（长期平均速率上限）。

每个请求消耗 1 个令牌；桶空时拒绝并返回 ``retry_after``（凑齐 1 个令牌所需秒数）。

设计要点：

- **Redis 模式**：用 WATCH/MULTI/EXEC 原子化「补充 token + 取 token + 计算
  retry_after + 写回」，与 :mod:`apps.system.distributed_lock` 一致，兼容
  fakeredis（不依赖 EVAL）。
- **本地降级**：``threading.Lock`` + ``time.monotonic`` 内存桶，单进程生效。
- **兼容旧接口**：:func:`check_rate_limit` 保留 ``(key, max_requests,
  window_seconds)`` 签名，内部转译为 ``capacity=max_requests``、
  ``refill_rate=max_requests/window_seconds``，行为等价于「窗口内最多 N 次」
  且支持突发与逐步恢复。
- **降级放行**：Redis 故障时临时放行，避免限流器故障导致服务不可用。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import redis

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 限流键前缀，与幂等/锁/配额的命名空间隔离。
_KEY_PREFIX = "rdbase:rate"

# 令牌桶默认每次请求消耗的令牌数。
_DEFAULT_COST = 1


class _RateBackend(Protocol):
    """令牌桶限流后端协议."""

    def check_token_bucket(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        cost: float = _DEFAULT_COST,
    ) -> tuple[bool, int]:
        """检查并消费令牌.

        Args:
            key: 限流 key（不含前缀）。
            capacity: 桶容量（最大令牌数）。
            refill_rate: 每秒补充的令牌数。
            cost: 本次请求消耗的令牌数，默认 1。

        Returns:
            ``(allowed, retry_after_seconds)``：允许时 ``retry_after=0``；
            超限时 ``retry_after>=1``（凑齐 ``cost`` 个令牌所需秒数）。
        """
        ...

    def reset(self, key: str) -> None:
        """清除指定 key 的桶状态（测试用）."""
        ...


@dataclass
class _LocalBucket:
    """本地令牌桶状态."""

    tokens: float
    last_refill: float  # monotonic 时间戳


class _LocalBackend:
    """进程内令牌桶后端.

    用 ``threading.Lock`` 保护 check-then-act，``time.monotonic`` 计时。
    仅本进程生效，适用于 Redis 不可用时的降级场景。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _LocalBucket] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def check_token_bucket(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        cost: float = _DEFAULT_COST,
    ) -> tuple[bool, int]:
        with self._lock:
            now = self._now()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _LocalBucket(tokens=capacity, last_refill=now)
                self._buckets[key] = bucket
            # 补充令牌（不超过容量）
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_rate)
            bucket.last_refill = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0
            # 不足：计算凑齐 cost 所需秒数
            deficit = cost - bucket.tokens
            retry_after = self._calc_retry_after(deficit, refill_rate)
            return False, retry_after

    @staticmethod
    def _calc_retry_after(deficit: float, refill_rate: float) -> int:
        """计算凑齐 deficit 个令牌所需秒数（向上取整，至少 1）."""
        if refill_rate <= 0:
            return 1
        return max(1, math.ceil(deficit / refill_rate))

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


class _RedisBackend:
    """Redis 令牌桶后端.

    用 WATCH/MULTI/EXEC 原子化「读 → 补充 → 决策 → 写回」，与
    :mod:`apps.system.distributed_lock` 一致，兼容 fakeredis（不依赖 EVAL）。
    跨 worker 共享桶状态，多进程协同限流。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, key: str) -> str:
        return f"{_KEY_PREFIX}:{key}"

    @staticmethod
    def _now() -> float:
        # Redis 端用 TIME 命令更精确，但为简化实现与本地保持一致用客户端时间。
        # 多 worker 时钟漂移在秒级限流场景可接受。
        return time.time()

    @staticmethod
    def _calc_retry_after(deficit: float, refill_rate: float) -> int:
        if refill_rate <= 0:
            return 1
        return max(1, math.ceil(deficit / refill_rate))

    def check_token_bucket(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        cost: float = _DEFAULT_COST,
    ) -> tuple[bool, int]:
        redis_key = self._k(key)
        now = self._now()
        # WATCH 重试上限：极端高并发下避免无限重试。
        max_retries = 5
        for _attempt in range(max_retries):
            try:
                allowed, retry_after = self._try_once(redis_key, capacity, refill_rate, cost, now)
                return allowed, retry_after
            except redis.WatchError:
                # 其他 worker 修改了 key，重试（重新读最新状态）。
                now = self._now()
                continue
            except redis.RedisError as exc:
                logger.warning("Redis 令牌桶限流失败 key=%s: %s，临时放行", key, exc)
                return True, 0
        # 重试耗尽：放行避免限流器故障影响业务。
        logger.warning(
            "Redis 令牌桶 WATCH 重试 %d 次仍冲突 key=%s，临时放行",
            max_retries,
            key,
        )
        return True, 0

    def _try_once(
        self,
        redis_key: str,
        capacity: float,
        refill_rate: float,
        cost: float,
        now: float,
    ) -> tuple[bool, int]:
        """单次 WATCH/MULTI/EXEC 尝试.

        Raises:
            redis.WatchError: WATCH 期间 key 被其他客户端修改。
        """
        with self._client.pipeline() as pipe:
            pipe.watch(redis_key)
            data = pipe.hgetall(redis_key)
            if data:
                try:
                    tokens = float(data.get("tokens", capacity))
                    last_refill = float(data.get("last_refill", now))
                except (TypeError, ValueError):
                    tokens = capacity
                    last_refill = now
            else:
                tokens = capacity
                last_refill = now
            # 补充令牌
            elapsed = max(0.0, now - last_refill)
            tokens = min(capacity, tokens + elapsed * refill_rate)
            # 决策
            if tokens >= cost:
                tokens -= cost
                allowed = True
                retry_after = 0
            else:
                deficit = cost - tokens
                retry_after = self._calc_retry_after(deficit, refill_rate)
                allowed = False
            # 写回
            pipe.multi()
            pipe.hset(
                redis_key,
                mapping={
                    "tokens": tokens,
                    "last_refill": now,
                },
            )
            # TTL：桶满所需时间 + 60s 缓冲，避免冷数据长期驻留。
            ttl = int(capacity / refill_rate) + 60 if refill_rate > 0 else 3600
            pipe.expire(redis_key, ttl)
            pipe.execute()
            return allowed, retry_after

    def reset(self, key: str) -> None:
        try:
            self._client.delete(self._k(key))
        except redis.RedisError:
            logger.debug("清除限流桶失败 key=%s", key, exc_info=True)


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
            logger.info("限流后端：Redis 令牌桶（多 worker 跨进程生效）")
        else:
            _backend = _LocalBackend()
            logger.warning("Redis 未配置，限流降级为本地令牌桶（仅本进程生效）")
        return _backend


def reset_rate_limiter() -> None:
    """重置后端单例（仅测试用）.

    清空缓存的后端实例，下次调用重新按 settings 解析后端。
    """
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _backend_lock:
        _backend = None


def check_token_bucket(
    key: str,
    capacity: float,
    refill_rate: float,
    cost: float = _DEFAULT_COST,
) -> tuple[bool, int]:
    """检查令牌桶速率限制并消费令牌.

    Args:
        key: 限流维度 key（如 ``dataset_write:{token_prefix}``）。
        capacity: 桶容量（允许的突发上限）。
        refill_rate: 每秒补充的令牌数（长期平均速率上限）。
        cost: 本次请求消耗的令牌数，默认 1。

    Returns:
        ``(allowed, retry_after_seconds)``：允许时 ``allowed=True, retry_after=0``；
        超限时 ``allowed=False, retry_after>=1``（供 429 响应使用）。
    """
    return _resolve_backend().check_token_bucket(key, capacity, refill_rate, cost)


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    """检查速率限制并计数（兼容旧接口）.

    内部转译为令牌桶语义：``capacity=max_requests``、
    ``refill_rate=max_requests/window_seconds``。

    与原固定窗口的行为差异：

    - 突发支持：窗口起始即有 ``max_requests`` 个令牌可用，可瞬时消耗。
    - 逐步恢复：消耗后按 ``refill_rate`` 持续补充，无需等窗口过期。
    - 等价场景：``max_requests`` 次快速调用后第 ``max_requests+1`` 次仍被拒，
      与固定窗口一致。

    Args:
        key: 限流维度 key。
        max_requests: 窗口内允许的最大请求数（桶容量）。
        window_seconds: 窗口时长（秒），与 max_requests 共同决定补充速率。

    Returns:
        ``(allowed, retry_after_seconds)``。
    """
    if window_seconds <= 0:
        # 不合理入参：拒绝以暴露调用方 bug。
        raise ValueError("window_seconds 必须 > 0")
    if max_requests <= 0:
        raise ValueError("max_requests 必须 > 0")
    refill_rate = max_requests / window_seconds
    return _resolve_backend().check_token_bucket(key, float(max_requests), refill_rate)


def reset_rate_key(key: str) -> None:
    """清除指定 key 的桶状态（测试用）."""
    _resolve_backend().reset(key)


__all__ = [
    "check_rate_limit",
    "check_token_bucket",
    "reset_rate_key",
    "reset_rate_limiter",
]
