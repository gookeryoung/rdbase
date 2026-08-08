"""分布式锁测试.

覆盖：本地内存后端、Redis 共享后端、acquire/release 语义、Lua 释放防误释放、
上下文管理器、strict 模式、锁超时自动释放、list_lock_info、API 暴露与权限。
"""

from __future__ import annotations

import json
import time

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.system.distributed_lock import (
    DEFAULT_CONFIG,
    DistributedLock,
    LockAcquireError,
    LockConfig,
    LockUnavailableError,
    get_lock,
    list_lock_info,
)
from django.test import Client, override_settings

# ---------- 本地内存后端 ----------


class TestLocalBackend:
    """本地内存后端下锁语义."""

    def test_acquire_first_succeeds(self) -> None:
        """首次 acquire 成功."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("local:1")
            assert lock.acquire() is True
            assert lock.held is True

    def test_acquire_second_fails(self) -> None:
        """已持有时第二次 acquire 失败."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = DistributedLock("local:2")
            lock2 = DistributedLock("local:2")
            assert lock1.acquire() is True
            assert lock2.acquire() is False

    def test_release_allows_reacquire(self) -> None:
        """release 后可重新 acquire."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = DistributedLock("local:3")
            lock1.acquire()
            lock1.release()
            lock2 = DistributedLock("local:3")
            assert lock2.acquire() is True

    def test_release_wrong_value_fails(self) -> None:
        """非持有者 release 不释放锁."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = DistributedLock("local:4")
            lock1.acquire()
            # 另一实例 release 不会误释放（本地后端 value 校验）
            other = DistributedLock("local:4")
            assert other.release() is False
            assert lock1.held is True

    def test_release_when_not_held_returns_true(self) -> None:
        """未持有锁时 release 返回 True（幂等）."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("local:5")
            assert lock.release() is True

    def test_acquire_idempotent_when_held(self) -> None:
        """同一实例已持有锁时再次 acquire 返回 True."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("local:6")
            lock.acquire()
            assert lock.acquire() is True  # 已持有，直接放行

    def test_lock_info_held(self) -> None:
        """持有锁时 info 返回 held=True."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("local:7", LockConfig(ttl_seconds=30))
            lock.acquire()
            info = lock.info()
            assert info.held is True
            assert info.name == "local:7"
            assert 0 <= info.ttl <= 30

    def test_lock_info_not_held(self) -> None:
        """未持有时 info 返回 held=False."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("local:8")
            info = lock.info()
            assert info.held is False
            assert info.ttl == 0

    def test_lock_expires_after_ttl(self) -> None:
        """锁超时后自动释放."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = DistributedLock("expire:1", LockConfig(ttl_seconds=1))
            lock1.acquire()
            time.sleep(1.1)
            lock2 = DistributedLock("expire:1")
            assert lock2.acquire() is True

    def test_list_lock_info_empty(self) -> None:
        """无锁时 list_lock_info 返回空."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            assert list_lock_info() == []

    def test_list_lock_info_returns_held(self) -> None:
        """list_lock_info 返回持有的锁."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = DistributedLock("list:1")
            lock1.acquire()
            infos = list_lock_info()
            names = [i.name for i in infos]
            assert "list:1" in names


# ---------- Redis 共享后端 ----------


class TestRedisBackend:
    """Redis 共享后端下锁语义."""

    def test_redis_acquire_first_succeeds(self) -> None:
        """Redis 后端首次 acquire 成功."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock = DistributedLock("redis:1")
            assert lock.acquire() is True

    def test_redis_acquire_second_fails(self) -> None:
        """Redis 后端已持有时第二次 acquire 失败."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock1 = DistributedLock("redis:2")
            lock2 = DistributedLock("redis:2")
            assert lock1.acquire() is True
            assert lock2.acquire() is False

    def test_redis_release_allows_reacquire(self) -> None:
        """Redis 后端 release 后可重新 acquire."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock1 = DistributedLock("redis:3")
            lock1.acquire()
            lock1.release()
            lock2 = DistributedLock("redis:3")
            assert lock2.acquire() is True

    def test_redis_release_wrong_value_fails(self) -> None:
        """Redis Lua 脚本：非持有者 release 不释放."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock1 = DistributedLock("redis:4")
            lock1.acquire()
            other = DistributedLock("redis:4")
            assert other.release() is False
            # 锁仍被 lock1 持有
            assert lock1.info().held is True

    def test_redis_lock_info(self) -> None:
        """Redis 后端 info 返回正确状态."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock = DistributedLock("redis:5", LockConfig(ttl_seconds=30))
            lock.acquire()
            info = lock.info()
            assert info.held is True
            assert info.name == "redis:5"
            assert 0 <= info.ttl <= 30

    def test_redis_lock_expires(self) -> None:
        """Redis 后端锁超时自动释放."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock1 = DistributedLock("redis:6", LockConfig(ttl_seconds=1))
            lock1.acquire()
            time.sleep(1.2)
            lock2 = DistributedLock("redis:6")
            assert lock2.acquire() is True

    def test_redis_list_lock_info(self) -> None:
        """Redis 后端 list_lock_info 返回持有的锁."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            lock1 = DistributedLock("rlist:1")
            lock1.acquire()
            lock2 = DistributedLock("rlist:2")
            lock2.acquire()
            infos = list_lock_info()
            names = [i.name for i in infos]
            assert "rlist:1" in names
            assert "rlist:2" in names

    def test_redis_list_with_pattern(self) -> None:
        """list_lock_info 支持 pattern 过滤."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            DistributedLock("pat:sync:1").acquire()
            DistributedLock("pat:ingest:2").acquire()
            infos = list_lock_info("pat:sync:*")
            names = [i.name for i in infos]
            assert names == ["pat:sync:1"]


# ---------- 上下文管理器 ----------


class TestContextManager:
    """with 语句用法."""

    def test_context_manager_acquires_and_releases(self) -> None:
        """with 块内持有锁，退出后释放."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            with DistributedLock("ctx:1") as lock:
                assert lock.held is True
            # 退出后已释放
            assert DistributedLock("ctx:1").acquire() is True

    def test_context_manager_raises_on_conflict(self) -> None:
        """锁被占用时 with 抛 LockAcquireError."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            holder = DistributedLock("ctx:2")
            holder.acquire()
            with pytest.raises(LockAcquireError) as exc_info, DistributedLock("ctx:2"):
                pass
            assert exc_info.value.name == "ctx:2"
            assert exc_info.value.ttl > 0

    def test_context_manager_releases_on_exception(self) -> None:
        """with 块内抛异常时仍释放锁."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            with pytest.raises(ValueError), DistributedLock("ctx:3"):
                raise ValueError("boom")
            # 锁已释放，可重新获取
            assert DistributedLock("ctx:3").acquire() is True


# ---------- strict 模式 ----------


class TestStrictMode:
    """strict 模式下 Redis 不可用拒绝加锁."""

    def test_strict_raises_when_redis_unavailable(self) -> None:
        """strict=True 且 Redis 不可用时抛 LockUnavailableError."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("strict:1", LockConfig(strict=True))
            with pytest.raises(LockUnavailableError):
                lock.acquire()

    def test_non_strict_degrades_when_redis_unavailable(self) -> None:
        """strict=False 且 Redis 不可用时降级为无锁（放行）."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = DistributedLock("strict:2", LockConfig(strict=False))
            assert lock.acquire() is True
            assert lock.held is True
            assert lock.release() is True


# ---------- 默认配置 ----------


class TestConfig:
    """默认配置符合 req-03 决策."""

    def test_default_ttl_30s(self) -> None:
        """默认 TTL 30s（req-03 第19行）."""
        assert DEFAULT_CONFIG.ttl_seconds == 30

    def test_default_strict_false(self) -> None:
        """默认非 strict（降级放行，不阻断业务）."""
        assert DEFAULT_CONFIG.strict is False

    def test_config_frozen(self) -> None:
        """配置为 frozen dataclass."""
        import dataclasses

        cfg = LockConfig(ttl_seconds=60)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.ttl_seconds = 120  # type: ignore[misc]


# ---------- get_lock 工厂 ----------


class TestGetLock:
    """get_lock 工厂函数."""

    def test_get_lock_returns_new_instance(self) -> None:
        """get_lock 每次返回新实例（独立 token）."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock1 = get_lock("factory:1")
            lock2 = get_lock("factory:1")
            assert lock1 is not lock2
            assert lock1.name == "factory:1"

    def test_get_lock_with_config(self) -> None:
        """get_lock 接受自定义配置."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            lock = get_lock("factory:2", LockConfig(ttl_seconds=99))
            assert lock.config.ttl_seconds == 99


# ---------- API ----------


def _auth_header(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.mark.django_db
def test_locks_api_admin_ok(admin_user: User) -> None:
    """管理员访问 /api/v1/system/locks 返回 200 与列表结构."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        DistributedLock("api:sync:1").acquire()
        client = Client(**_auth_header(admin_user))
        response = client.get("/api/v1/system/locks")
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["total"] >= 1
        item = body["items"][0]
        assert "name" in item
        assert "held" in item
        assert "ttl" in item


@pytest.mark.django_db
def test_locks_api_forbidden_for_viewer(regular_user: User) -> None:
    """viewer 访问应返回 403."""
    client = Client(**_auth_header(regular_user))
    response = client.get("/api/v1/system/locks")
    assert response.status_code == 403


@pytest.mark.django_db
def test_locks_api_unauth_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = client.get("/api/v1/system/locks")
    assert response.status_code == 401


@pytest.mark.django_db
def test_locks_api_empty_when_no_locks(admin_user: User) -> None:
    """无锁时返回空列表."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        client = Client(**_auth_header(admin_user))
        response = client.get("/api/v1/system/locks")
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["total"] == 0
        assert body["items"] == []


@pytest.mark.django_db
def test_locks_api_local_backend_admin_ok(admin_user: User) -> None:
    """本地内存后端下 API 也能返回锁状态."""
    with override_settings(REDIS_FAKE=False, REDIS_URL=""):
        DistributedLock("localapi:1").acquire()
        client = Client(**_auth_header(admin_user))
        response = client.get("/api/v1/system/locks")
        assert response.status_code == 200
        body = json.loads(response.content)
        names = [i["name"] for i in body["items"]]
        assert "localapi:1" in names
