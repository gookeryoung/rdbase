"""Redis 客户端单例测试.

覆盖：未配置降级、fakeredis 注入、真实 URL 客户端构造、单例复用、ping、重置与关闭。
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest
import redis
from apps.system import redis_client
from django.test import override_settings


@pytest.fixture(autouse=True)
def _reset_redis() -> Any:
    """每个测试前后清空 Redis 单例，避免相互污染."""
    redis_client.reset_redis_client()
    yield
    redis_client.reset_redis_client()


def test_get_redis_returns_none_when_unconfigured() -> None:
    """未配置 REDIS_URL 且 REDIS_FAKE=False 时返回 None."""
    with override_settings(REDIS_URL="", REDIS_FAKE=False):
        assert redis_client.get_redis() is None


def test_get_redis_uses_fakeredis_when_fake_enabled() -> None:
    """REDIS_FAKE=True 时返回 fakeredis 实例."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        client = redis_client.get_redis()
        assert isinstance(client, fakeredis.FakeRedis)


def test_get_redis_returns_real_client_when_url_set() -> None:
    """配置 REDIS_URL 时返回 redis.Redis 客户端（不实际连接）."""
    with override_settings(REDIS_URL="redis://localhost:6379/0", REDIS_FAKE=False):
        client = redis_client.get_redis()
        assert isinstance(client, redis.Redis)


def test_get_redis_is_singleton() -> None:
    """多次调用返回同一实例."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        first = redis_client.get_redis()
        second = redis_client.get_redis()
        assert first is second


def test_ping_redis_unconfigured() -> None:
    """未配置时 ping 返回 (False, 含'未配置')."""
    with override_settings(REDIS_URL="", REDIS_FAKE=False):
        ok, msg = redis_client.ping_redis()
        assert ok is False
        assert "未配置" in msg


def test_ping_redis_ok_with_fake() -> None:
    """fakeredis 下 ping 应成功."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        ok, msg = redis_client.ping_redis()
        assert ok is True
        assert "正常" in msg


def test_ping_redis_failure_reports_error() -> None:
    """redis 异常时 ping 返回失败并带错误信息."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        client = redis_client.get_redis()
        assert client is not None
        # 注入抛异常的 ping，模拟连接中断
        client.ping = lambda: (_ for _ in ()).throw(redis.RedisError("simulated outage"))  # type: ignore[method-assignment]
        ok, msg = redis_client.ping_redis()
        assert ok is False
        assert "失败" in msg


def test_reset_redis_client_clears_singleton() -> None:
    """reset 后单例清空，下次 get_redis 重新构造."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        first = redis_client.get_redis()
        redis_client.reset_redis_client()
        second = redis_client.get_redis()
        assert first is not second


def test_close_redis_clears_singleton() -> None:
    """close_redis 后单例清空."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        client = redis_client.get_redis()
        assert client is not None
        redis_client.close_redis()
        # 再次 get_redis 在未配置时应返回 None（close 已清空 _initialized）
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            assert redis_client.get_redis() is None
