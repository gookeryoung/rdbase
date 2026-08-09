"""速率限制器单元测试（令牌桶实现）.

覆盖：

- 兼容旧 ``check_rate_limit(key, max_requests, window_seconds)`` 接口，
  Redis（fakeredis）与本地降级两种后端。
- 新 ``check_token_bucket(key, capacity, refill_rate)`` 接口的令牌桶语义：
  突发支持、逐步恢复、retry_after 计算、cost 参数。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from apps.system import rate_limiter
from django.test import override_settings


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """每个测试前后重置限流后端单例."""
    rate_limiter.reset_rate_limiter()
    yield
    rate_limiter.reset_rate_limiter()


# ---------- 本地降级模式 ----------


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_allows_under_limit() -> None:
    """本地模式：未超限时应放行."""
    allowed, retry_after = rate_limiter.check_rate_limit("k1", max_requests=3, window_seconds=60)
    assert allowed is True
    assert retry_after == 0


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_blocks_over_limit() -> None:
    """本地模式：超出上限应拒绝并返回剩余秒数."""
    key = "k2"
    for _ in range(3):
        rate_limiter.check_rate_limit(key, max_requests=3, window_seconds=60)
    allowed, retry_after = rate_limiter.check_rate_limit(key, max_requests=3, window_seconds=60)
    assert allowed is False
    assert retry_after >= 1


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_window_expiry() -> None:
    """本地模式：窗口过期后应重新计数."""
    key = "k3"
    rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=1)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=1)
    assert allowed is False
    time.sleep(1.1)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=1)
    assert allowed is True


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_reset_key() -> None:
    """本地模式：reset_key 清除计数后应放行."""
    key = "k4"
    rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    assert allowed is False
    rate_limiter.reset_rate_key(key)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    assert allowed is True


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_independent_keys() -> None:
    """本地模式：不同 key 计数独立."""
    rate_limiter.check_rate_limit("a", max_requests=1, window_seconds=60)
    allowed, _ = rate_limiter.check_rate_limit("b", max_requests=1, window_seconds=60)
    assert allowed is True


# ---------- Redis（fakeredis）模式 ----------


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_allows_under_limit() -> None:
    """Redis 模式：未超限应放行."""
    allowed, retry_after = rate_limiter.check_rate_limit("rk1", max_requests=2, window_seconds=60)
    assert allowed is True
    assert retry_after == 0


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_blocks_over_limit() -> None:
    """Redis 模式：超限应拒绝并返回 retry_after."""
    key = "rk2"
    rate_limiter.check_rate_limit(key, max_requests=2, window_seconds=60)
    rate_limiter.check_rate_limit(key, max_requests=2, window_seconds=60)
    allowed, retry_after = rate_limiter.check_rate_limit(key, max_requests=2, window_seconds=60)
    assert allowed is False
    assert retry_after >= 1


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_reset_key() -> None:
    """Redis 模式：reset_key 清除计数后应放行."""
    key = "rk3"
    rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    assert allowed is False
    rate_limiter.reset_rate_key(key)
    allowed, _ = rate_limiter.check_rate_limit(key, max_requests=1, window_seconds=60)
    assert allowed is True


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_independent_keys() -> None:
    """Redis 模式：不同 key 计数独立."""
    rate_limiter.check_rate_limit("ra", max_requests=1, window_seconds=60)
    allowed, _ = rate_limiter.check_rate_limit("rb", max_requests=1, window_seconds=60)
    assert allowed is True


# ================================================================
# check_rate_limit 入参校验
# ================================================================


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_check_rate_limit_rejects_nonpositive_window() -> None:
    """window_seconds <= 0 应抛 ValueError."""
    with pytest.raises(ValueError):
        rate_limiter.check_rate_limit("k", max_requests=1, window_seconds=0)


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_check_rate_limit_rejects_nonpositive_max() -> None:
    """max_requests <= 0 应抛 ValueError."""
    with pytest.raises(ValueError):
        rate_limiter.check_rate_limit("k", max_requests=0, window_seconds=60)


# ================================================================
# 令牌桶（check_token_bucket）语义测试
# ================================================================


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_burst_capacity() -> None:
    """本地令牌桶：起始即有 capacity 个令牌，可瞬时消耗."""
    key = "tb-burst-local"
    # 容量 5，立即消耗 5 次都应放行
    for _ in range(5):
        allowed, retry_after = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0)
        assert allowed is True
        assert retry_after == 0
    # 第 6 次应被拒
    allowed, retry_after = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0)
    assert allowed is False
    assert retry_after >= 1


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_gradual_refill() -> None:
    """本地令牌桶：消耗后等待应逐步恢复令牌."""
    key = "tb-refill-local"
    # 容量 1，先消耗
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=10.0)
    assert allowed is True
    # 立即再请求应被拒
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=10.0)
    assert allowed is False
    # refill_rate=10/s，等待 0.15s 应至少补充 1 个令牌
    time.sleep(0.15)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=10.0)
    assert allowed is True


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_retry_after_calculation() -> None:
    """本地令牌桶：retry_after 应按 deficit / refill_rate 向上取整."""
    key = "tb-retry-local"
    # 容量 0（直接构造空桶场景）：先消耗 1 个令牌（容量 1）
    rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=2.0)
    # 桶空，refill_rate=2/s，deficit=1，retry_after = ceil(1/2) = 1
    _, retry_after = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=2.0)
    assert retry_after == 1


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_cost_param() -> None:
    """本地令牌桶：cost 参数支持一次消耗多个令牌."""
    key = "tb-cost-local"
    # 容量 5，cost=3：首次消耗 3 个，应放行
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0, cost=3.0)
    assert allowed is True
    # 剩余 2 个，再 cost=3 应被拒
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0, cost=3.0)
    assert allowed is False


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_reset_key() -> None:
    """本地令牌桶：reset_key 清除桶状态后应回到满桶."""
    key = "tb-reset-local"
    rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    assert allowed is False
    rate_limiter.reset_rate_key(key)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    assert allowed is True


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_token_bucket_zero_refill_blocks_forever() -> None:
    """本地令牌桶：refill_rate=0 时桶空后永久拒绝，retry_after 至少 1."""
    key = "tb-zero-refill"
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=0.0)
    assert allowed is True
    allowed, retry_after = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=0.0)
    assert allowed is False
    assert retry_after >= 1


# ---------- Redis（fakeredis）令牌桶 ----------


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_burst_capacity() -> None:
    """Redis 令牌桶：起始即有 capacity 个令牌，可瞬时消耗."""
    key = "tb-burst-redis"
    for _ in range(3):
        allowed, retry_after = rate_limiter.check_token_bucket(key, capacity=3.0, refill_rate=1.0)
        assert allowed is True
        assert retry_after == 0
    allowed, retry_after = rate_limiter.check_token_bucket(key, capacity=3.0, refill_rate=1.0)
    assert allowed is False
    assert retry_after >= 1


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_gradual_refill() -> None:
    """Redis 令牌桶：消耗后等待应逐步恢复令牌."""
    key = "tb-refill-redis"
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=20.0)
    assert allowed is True
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=20.0)
    assert allowed is False
    # refill_rate=20/s，等待 0.1s 应补充 2 个令牌
    time.sleep(0.1)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=20.0)
    assert allowed is True


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_retry_after() -> None:
    """Redis 令牌桶：retry_after 应按 deficit / refill_rate 向上取整."""
    key = "tb-retry-redis"
    rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    _, retry_after = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    # deficit=1, refill_rate=1/s → retry_after=1
    assert retry_after == 1


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_cost_param() -> None:
    """Redis 令牌桶：cost 参数支持一次消耗多个令牌."""
    key = "tb-cost-redis"
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0, cost=3.0)
    assert allowed is True
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=5.0, refill_rate=1.0, cost=3.0)
    assert allowed is False


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_reset_key() -> None:
    """Redis 令牌桶：reset_key 清除桶状态后应回到满桶."""
    key = "tb-reset-redis"
    rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    assert allowed is False
    rate_limiter.reset_rate_key(key)
    allowed, _ = rate_limiter.check_token_bucket(key, capacity=1.0, refill_rate=1.0)
    assert allowed is True


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_token_bucket_independent_keys() -> None:
    """Redis 令牌桶：不同 key 桶状态独立."""
    rate_limiter.check_token_bucket("tba", capacity=1.0, refill_rate=1.0)
    allowed, _ = rate_limiter.check_token_bucket("tbb", capacity=1.0, refill_rate=1.0)
    assert allowed is True
