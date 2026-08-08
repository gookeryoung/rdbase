"""每日写入配额单元测试.

覆盖 Redis（fakeredis）与本地降级两种后端。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from apps.system import quota
from django.test import override_settings


@pytest.fixture(autouse=True)
def _reset_quota() -> Iterator[None]:
    """每个测试前后重置配额后端单例."""
    quota.reset_quota()
    yield
    quota.reset_quota()


# ---------- 本地降级模式 ----------


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_allows_under_limit() -> None:
    """本地模式：未超额应放行并返回剩余."""
    allowed, remaining = quota.check_and_consume_quota("q1", rows=10, daily_limit=100)
    assert allowed is True
    assert remaining == 90


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_blocks_over_limit() -> None:
    """本地模式：超额应拒绝."""
    key = "q2"
    quota.check_and_consume_quota(key, rows=80, daily_limit=100)
    allowed, remaining = quota.check_and_consume_quota(key, rows=30, daily_limit=100)
    assert allowed is False
    assert remaining == 0


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_exact_boundary() -> None:
    """本地模式：恰好用尽应放行且 remaining=0."""
    key = "q3"
    quota.check_and_consume_quota(key, rows=80, daily_limit=100)
    allowed, remaining = quota.check_and_consume_quota(key, rows=20, daily_limit=100)
    assert allowed is True
    assert remaining == 0


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_reset_key() -> None:
    """本地模式：reset_key 清除计数后重新可用."""
    key = "q4"
    quota.check_and_consume_quota(key, rows=100, daily_limit=100)
    allowed, _ = quota.check_and_consume_quota(key, rows=1, daily_limit=100)
    assert allowed is False
    quota.reset_quota_key(key)
    allowed, remaining = quota.check_and_consume_quota(key, rows=10, daily_limit=100)
    assert allowed is True
    assert remaining == 90


@override_settings(REDIS_FAKE=False, REDIS_URL="")
def test_local_independent_keys() -> None:
    """本地模式：不同 key 配额独立."""
    quota.check_and_consume_quota("qa", rows=100, daily_limit=100)
    allowed, _ = quota.check_and_consume_quota("qb", rows=10, daily_limit=100)
    assert allowed is True


# ---------- Redis（fakeredis）模式 ----------


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_allows_under_limit() -> None:
    """Redis 模式：未超额应放行."""
    allowed, remaining = quota.check_and_consume_quota("rq1", rows=10, daily_limit=100)
    assert allowed is True
    assert remaining == 90


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_blocks_over_limit() -> None:
    """Redis 模式：超额应拒绝."""
    key = "rq2"
    quota.check_and_consume_quota(key, rows=80, daily_limit=100)
    allowed, remaining = quota.check_and_consume_quota(key, rows=30, daily_limit=100)
    assert allowed is False
    assert remaining == 0


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_accumulates_across_calls() -> None:
    """Redis 模式：多次调用累加计数."""
    key = "rq3"
    quota.check_and_consume_quota(key, rows=30, daily_limit=100)
    _, remaining = quota.check_and_consume_quota(key, rows=20, daily_limit=100)
    assert remaining == 50


@override_settings(REDIS_FAKE=True, REDIS_URL="")
def test_redis_reset_key() -> None:
    """Redis 模式：reset_key 清除计数后重新可用."""
    key = "rq4"
    quota.check_and_consume_quota(key, rows=100, daily_limit=100)
    allowed, _ = quota.check_and_consume_quota(key, rows=1, daily_limit=100)
    assert allowed is False
    quota.reset_quota_key(key)
    allowed, _ = quota.check_and_consume_quota(key, rows=10, daily_limit=100)
    assert allowed is True
