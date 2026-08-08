"""熔断器测试.

覆盖：三态状态机（CLOSED/OPEN/HALF_OPEN）、本地内存后端、Redis 共享后端、
单例注册、snapshot 快照、API 暴露、权限控制。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.system.circuit_breaker import (
    DEFAULT_CONFIG,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    get_breaker,
    list_breakers,
    reset_backend,
)
from apps.system.redis_client import reset_redis_client
from django.test import Client, override_settings


@pytest.fixture(autouse=True)
def _reset() -> Any:
    """每个测试前后重置后端与 breaker 缓存（与 conftest 协同，确保隔离）."""
    reset_redis_client()
    reset_backend()
    yield
    reset_redis_client()
    reset_backend()


# ---------- 本地内存后端状态机 ----------


class TestLocalBackendStateMachine:
    """本地内存后端下熔断器三态迁移."""

    def test_initial_state_is_closed(self) -> None:
        """新建 breaker 默认 CLOSED."""
        breaker = get_breaker("test:1")
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_failure_below_threshold_stays_closed(self) -> None:
        """失败未达阈值仍 CLOSED."""
        breaker = get_breaker("test:2", CircuitBreakerConfig(failure_threshold=3))
        breaker.on_failure()
        breaker.on_failure()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 2

    def test_failure_at_threshold_opens(self) -> None:
        """失败达阈值转 OPEN."""
        breaker = get_breaker("test:3", CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            breaker.on_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.opened_at > 0

    def test_success_resets_failure_count_in_closed(self) -> None:
        """CLOSED 下成功重置失败计数."""
        breaker = get_breaker("test:4", CircuitBreakerConfig(failure_threshold=3))
        breaker.on_failure()
        breaker.on_failure()
        breaker.on_success()
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED

    def test_before_call_rejects_when_open(self) -> None:
        """OPEN 状态 before_call 抛 CircuitOpenError."""
        breaker = get_breaker("test:5", CircuitBreakerConfig(failure_threshold=1, open_seconds=60))
        breaker.on_failure()
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.before_call()
        assert exc_info.value.state == CircuitState.OPEN
        assert exc_info.value.retry_after > 0

    def test_open_transitions_to_half_open_after_timeout(self) -> None:
        """OPEN 到期转 HALF_OPEN."""
        breaker = get_breaker(
            "test:6",
            CircuitBreakerConfig(failure_threshold=1, open_seconds=0.0, half_open_max_calls=1),
        )
        breaker.on_failure()
        assert breaker.state == CircuitState.OPEN
        # open_seconds=0，下次 before_call 应转 HALF_OPEN 并放行
        breaker.before_call()
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self) -> None:
        """HALF_OPEN 探测成功转 CLOSED."""
        breaker = get_breaker(
            "test:7",
            CircuitBreakerConfig(failure_threshold=1, open_seconds=0.0, half_open_max_calls=1),
        )
        breaker.on_failure()
        breaker.before_call()  # 转 HALF_OPEN
        breaker.on_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_failure_reopens(self) -> None:
        """HALF_OPEN 探测失败转 OPEN."""
        breaker = get_breaker(
            "test:8",
            CircuitBreakerConfig(failure_threshold=1, open_seconds=0.0, half_open_max_calls=1),
        )
        breaker.on_failure()
        breaker.before_call()  # 转 HALF_OPEN
        breaker.on_failure()
        assert breaker.state == CircuitState.OPEN

    def test_half_open_rejects_excess_calls(self) -> None:
        """HALF_OPEN 探测调用数达上限后拒绝."""
        breaker = get_breaker(
            "test:9",
            CircuitBreakerConfig(failure_threshold=1, open_seconds=0.0, half_open_max_calls=2),
        )
        breaker.on_failure()
        breaker.before_call()  # 转 HALF_OPEN，calls=1
        breaker.before_call()  # calls=2
        assert breaker.half_open_calls == 2
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.before_call()
        assert exc_info.value.state == CircuitState.HALF_OPEN


# ---------- Redis 共享后端 ----------


class TestRedisBackend:
    """Redis 共享后端下熔断器行为."""

    def test_backend_resolves_to_redis_when_configured(self) -> None:
        """配置 REDIS_FAKE 时后端应为 _RedisBackend."""
        from apps.system.circuit_breaker import _RedisBackend, _resolve_backend

        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            backend = _resolve_backend()
            assert isinstance(backend, _RedisBackend)

    def test_redis_backend_shares_state_across_breakers(self) -> None:
        """同一 name 的两个 breaker 实例共享 Redis 状态."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            # 通过 reset_backend 强制重新解析后端为 Redis
            reset_backend()
            b1 = get_breaker("shared:1", CircuitBreakerConfig(failure_threshold=2))
            b2 = get_breaker("shared:1", CircuitBreakerConfig(failure_threshold=2))
            b1.on_failure()
            # b2 应看到 b1 写入的失败计数
            assert b2.failure_count == 1

    def test_redis_backend_opens_on_threshold(self) -> None:
        """Redis 后端失败达阈值转 OPEN."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            reset_backend()
            breaker = get_breaker("shared:2", CircuitBreakerConfig(failure_threshold=2, open_seconds=60))
            breaker.on_failure()
            breaker.on_failure()
            assert breaker.state == CircuitState.OPEN

    def test_redis_backend_half_open_recovery(self) -> None:
        """Redis 后端 HALF_OPEN 探测成功恢复 CLOSED."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            reset_backend()
            breaker = get_breaker(
                "shared:3",
                CircuitBreakerConfig(failure_threshold=1, open_seconds=0.0, half_open_max_calls=1),
            )
            breaker.on_failure()
            breaker.before_call()  # OPEN -> HALF_OPEN
            breaker.on_success()
            assert breaker.state == CircuitState.CLOSED


# ---------- 单例与快照 ----------


class TestSingletonAndSnapshot:
    """breaker 单例注册与状态快照."""

    def test_get_breaker_returns_same_instance(self) -> None:
        """同 name 返回同一实例."""
        b1 = get_breaker("singleton:1")
        b2 = get_breaker("singleton:1")
        assert b1 is b2

    def test_get_breaker_ignores_config_after_creation(self) -> None:
        """已存在的 breaker 忽略新配置."""
        b1 = get_breaker("singleton:2", CircuitBreakerConfig(failure_threshold=10))
        b2 = get_breaker("singleton:2", CircuitBreakerConfig(failure_threshold=99))
        assert b2 is b1
        assert b2.config.failure_threshold == 10

    def test_list_breakers_sorted_by_name(self) -> None:
        """list_breakers 按 name 排序."""
        get_breaker("z:1")
        get_breaker("a:1")
        get_breaker("m:1")
        names = [b.name for b in list_breakers()]
        assert names == sorted(names)

    def test_snapshot_contains_all_fields(self) -> None:
        """snapshot 返回完整字段."""
        breaker = get_breaker("snap:1", CircuitBreakerConfig(failure_threshold=5, open_seconds=60))
        snap = breaker.snapshot()
        assert snap["name"] == "snap:1"
        assert snap["state"] == "closed"
        assert snap["failure_count"] == 0
        assert snap["failure_threshold"] == 5
        assert snap["open_seconds"] == 60
        assert snap["half_open_max_calls"] == 3
        assert "retry_after" in snap

    def test_snapshot_retry_after_when_open(self) -> None:
        """OPEN 状态 snapshot 的 retry_after 为正."""
        breaker = get_breaker("snap:2", CircuitBreakerConfig(failure_threshold=1, open_seconds=60))
        breaker.on_failure()
        snap = breaker.snapshot()
        assert snap["state"] == "open"
        assert snap["retry_after"] > 0

    def test_reset_clears_state(self) -> None:
        """reset 强制回到 CLOSED."""
        breaker = get_breaker("snap:3", CircuitBreakerConfig(failure_threshold=1))
        breaker.on_failure()
        assert breaker.state == CircuitState.OPEN
        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0


# ---------- 典型用法集成 ----------


class TestTypicalUsage:
    """before_call/on_success/on_failure 典型调用序列."""

    def test_successful_call_flow(self) -> None:
        """成功调用：before_call 放行 -> on_success 重置."""
        breaker = get_breaker("usage:1", CircuitBreakerConfig(failure_threshold=3))
        breaker.on_failure()
        breaker.before_call()
        breaker.on_success()
        assert breaker.failure_count == 0

    def test_circuit_open_error_propagates(self) -> None:
        """OPEN 时调用方捕获 CircuitOpenError."""
        breaker = get_breaker("usage:2", CircuitBreakerConfig(failure_threshold=1, open_seconds=60))
        breaker.on_failure()
        with pytest.raises(CircuitOpenError):
            breaker.before_call()

    def test_recovery_after_open_timeout(self) -> None:
        """OPEN 到期 -> HALF_OPEN -> 成功 -> CLOSED 完整恢复路径."""
        breaker = get_breaker(
            "usage:3",
            CircuitBreakerConfig(failure_threshold=1, open_seconds=0.05, half_open_max_calls=1),
        )
        breaker.on_failure()
        assert breaker.state == CircuitState.OPEN
        time.sleep(0.06)
        breaker.before_call()  # 转 HALF_OPEN
        breaker.on_success()
        assert breaker.state == CircuitState.CLOSED


# ---------- API ----------


def _auth_header(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.mark.django_db
def test_circuit_states_api_admin_ok(admin_user: User) -> None:
    """管理员访问 /api/v1/system/circuit-states 返回 200 与列表结构."""
    get_breaker("api:sync:1")
    client = Client(**_auth_header(admin_user))
    response = client.get("/api/v1/system/circuit-states")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] >= 1
    item = body["items"][0]
    assert "name" in item
    assert "state" in item
    assert "failure_count" in item
    assert "retry_after" in item


@pytest.mark.django_db
def test_circuit_states_api_forbidden_for_viewer(regular_user: User) -> None:
    """viewer 访问应返回 403."""
    client = Client(**_auth_header(regular_user))
    response = client.get("/api/v1/system/circuit-states")
    assert response.status_code == 403


@pytest.mark.django_db
def test_circuit_states_api_unauth_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = client.get("/api/v1/system/circuit-states")
    assert response.status_code == 401


@pytest.mark.django_db
def test_circuit_states_api_empty_when_no_breakers(admin_user: User) -> None:
    """无 breaker 时返回空列表."""
    client = Client(**_auth_header(admin_user))
    response = client.get("/api/v1/system/circuit-states")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 0
    assert body["items"] == []


def test_default_config_values() -> None:
    """默认配置符合 req-03 决策（threshold=5/open_seconds=60/half_open_max=3）."""
    assert DEFAULT_CONFIG.failure_threshold == 5
    assert DEFAULT_CONFIG.open_seconds == 60.0
    assert DEFAULT_CONFIG.half_open_max_calls == 3
