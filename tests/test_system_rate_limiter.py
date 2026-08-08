"""速率限制器单元测试.

覆盖 Redis（fakeredis）与本地降级两种后端。
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
