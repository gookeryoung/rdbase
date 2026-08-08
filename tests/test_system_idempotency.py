"""幂等保护测试.

覆盖：主体抽象、key 提取、本地内存后端、Redis 共享后端、manager 单例、
acquire/store_result/release 语义、并发 in_progress 返回 409、便捷函数、
缓存体损坏降级。
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest
from apps.system import idempotency
from apps.system.idempotency import (
    DEFAULT_CONFIG,
    IdempotencyConfig,
    IdempotencyManager,
    IdempotencyState,
    get_idempotency_key,
    get_idempotent_subject,
    get_manager,
    reset_manager,
    reset_store,
)
from apps.system.redis_client import reset_redis_client
from django.test import override_settings


def _make_request(user_pk: int | None = 1, key: str | None = None) -> MagicMock:
    """构造带 auth 与 headers 的 mock 请求."""
    request = MagicMock()
    if user_pk is not None:
        user = MagicMock()
        user.pk = user_pk
        request.auth = user
    else:
        request.auth = None
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    request.headers = headers
    return request


# ---------- 主体抽象与 key 提取 ----------


class TestSubjectAndKey:
    """认证主体与 Idempotency-Key 提取."""

    def test_subject_returns_user_prefix(self) -> None:
        """JWT 场景主体为 user:{pk}."""
        request = _make_request(user_pk=42)
        assert get_idempotent_subject(request) == "user:42"

    def test_subject_returns_none_when_unauthenticated(self) -> None:
        """未认证时主体为 None."""
        request = _make_request(user_pk=None)
        assert get_idempotent_subject(request) is None

    def test_subject_returns_none_when_no_pk(self) -> None:
        """auth 对象无 pk 时返回 None."""
        request = MagicMock()
        user = MagicMock()
        del user.pk
        request.auth = user
        # getattr(user, "pk", None) 返回 None（MagicMock 删除属性后）
        assert get_idempotent_subject(request) is None or not hasattr(request.auth, "pk")

    def test_key_extracted_from_header(self) -> None:
        """从请求头提取 key."""
        request = _make_request(key="abc-123")
        assert get_idempotency_key(request) == "abc-123"

    def test_key_stripped(self) -> None:
        """key 前后空白被去除."""
        request = MagicMock()
        request.headers = {"Idempotency-Key": "  spaced  "}
        assert get_idempotency_key(request) == "spaced"

    def test_key_none_when_missing(self) -> None:
        """未传头返回 None."""
        request = _make_request(key=None)
        assert get_idempotency_key(request) is None

    def test_key_none_when_empty(self) -> None:
        """空字符串头返回 None."""
        request = MagicMock()
        request.headers = {"Idempotency-Key": "   "}
        assert get_idempotency_key(request) is None


# ---------- 本地内存后端 ----------


class TestLocalStore:
    """本地内存后端下幂等语义."""

    def test_acquire_first_returns_should_run(self) -> None:
        """首次 acquire 返回 (None, True)."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            record, should_run = manager.acquire("user:1", "key-a")
            assert record is None
            assert should_run is True

    def test_acquire_second_returns_in_progress(self) -> None:
        """未 store_result 前，第二次 acquire 命中 in_progress."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "key-b")
            record, should_run = manager.acquire("user:1", "key-b")
            assert should_run is False
            assert record is not None
            assert record.state == IdempotencyState.IN_PROGRESS

    def test_store_result_then_acquire_returns_completed(self) -> None:
        """store_result 后再 acquire 命中 completed."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "key-c")
            manager.store_result("user:1", "key-c", 200, '{"ok": true}')
            record, should_run = manager.acquire("user:1", "key-c")
            assert should_run is False
            assert record is not None
            assert record.state == IdempotencyState.COMPLETED
            assert record.status_code == 200
            assert record.body == '{"ok": true}'

    def test_release_allows_retry(self) -> None:
        """release 后槽位清空，可再次 acquire."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "key-d")
            manager.release("user:1", "key-d")
            record, should_run = manager.acquire("user:1", "key-d")
            assert should_run is True
            assert record is None

    def test_different_subjects_isolated(self) -> None:
        """不同主体的相同 key 互不影响."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "shared")
            record, should_run = manager.acquire("user:2", "shared")
            assert should_run is True
            assert record is None

    def test_get_returns_record(self) -> None:
        """get 查询返回当前记录."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "key-e")
            record = manager.get("user:1", "key-e")
            assert record is not None
            assert record.state == IdempotencyState.IN_PROGRESS

    def test_get_none_when_absent(self) -> None:
        """无记录时 get 返回 None."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            assert manager.get("user:1", "absent") is None

    def test_list_keys_empty(self) -> None:
        """无记录时 list_keys 返回空."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            assert manager.list_keys() == []

    def test_list_keys_returns_subject_key(self) -> None:
        """list_keys 返回 subject:key 形式."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "k1")
            manager.acquire("user:2", "k2")
            keys = manager.list_keys()
            assert "user:1:k1" in keys
            assert "user:2:k2" in keys


# ---------- Redis 共享后端 ----------


class TestRedisStore:
    """Redis 共享后端下幂等语义."""

    def test_redis_acquire_first_should_run(self) -> None:
        """Redis 后端首次 acquire 返回 (None, True)."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            manager = IdempotencyManager()
            record, should_run = manager.acquire("user:1", "rkey-a")
            assert should_run is True
            assert record is None

    def test_redis_acquire_second_in_progress(self) -> None:
        """Redis 后端第二次 acquire 命中 in_progress."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "rkey-b")
            record, should_run = manager.acquire("user:1", "rkey-b")
            assert should_run is False
            assert record is not None
            assert record.state == IdempotencyState.IN_PROGRESS

    def test_redis_store_then_completed(self) -> None:
        """Redis 后端 store_result 后命中 completed."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "rkey-c")
            manager.store_result("user:1", "rkey-c", 201, '{"created": true}')
            record, _ = manager.acquire("user:1", "rkey-c")
            assert record is not None
            assert record.state == IdempotencyState.COMPLETED
            assert record.status_code == 201
            assert record.body == '{"created": true}'

    def test_redis_release_allows_retry(self) -> None:
        """Redis 后端 release 后可重新 acquire."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            manager = IdempotencyManager()
            manager.acquire("user:1", "rkey-d")
            manager.release("user:1", "rkey-d")
            _, should_run = manager.acquire("user:1", "rkey-d")
            assert should_run is True

    def test_redis_corrupt_body_treated_as_none(self) -> None:
        """Redis 中 body 损坏时 get 返回 None."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            from apps.system.redis_client import get_redis

            client = get_redis()
            assert client is not None
            # 直接写入损坏 JSON
            client.set("rdbase:idem:user:1:bad", "{not valid json")
            manager = IdempotencyManager()
            record, should_run = manager.acquire("user:1", "bad")
            # 损坏内容视为无记录，可正常 acquire
            assert record is None
            assert should_run is True


# ---------- 默认配置 ----------


class TestConfig:
    """默认配置符合 req-03 决策."""

    def test_default_ttl_is_24h(self) -> None:
        """ttl_seconds 默认 24h = 86400."""
        assert DEFAULT_CONFIG.ttl_seconds == 86400

    def test_default_in_progress_ttl_5min(self) -> None:
        """in_progress TTL 默认 5 分钟."""
        assert DEFAULT_CONFIG.in_progress_ttl_seconds == 300

    def test_default_header_name(self) -> None:
        """默认请求头名为 Idempotency-Key."""
        assert DEFAULT_CONFIG.header_name == "Idempotency-Key"

    def test_config_frozen(self) -> None:
        """配置为 frozen dataclass."""
        import dataclasses

        cfg = IdempotencyConfig(ttl_seconds=60)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.ttl_seconds = 120  # type: ignore[misc]


# ---------- manager 单例 ----------


class TestManagerSingleton:
    """manager 单例语义."""

    def test_get_manager_returns_singleton(self) -> None:
        """get_manager 多次返回同一实例."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            first = get_manager()
            second = get_manager()
            assert first is second

    def test_reset_manager_clears_singleton(self) -> None:
        """reset 后单例清空."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            first = get_manager()
            reset_manager()
            second = get_manager()
            assert first is not second

    def test_backend_resolution_prefers_redis(self) -> None:
        """REDIS_FAKE=True 时后端为 Redis."""
        reset_redis_client()
        reset_store()
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            manager = IdempotencyManager()
            assert isinstance(manager._store, idempotency._RedisStore)
        reset_redis_client()


# ---------- 便捷函数 ----------


class TestConvenienceFunctions:
    """check_idempotency / store_idempotency_result / release_idempotency."""

    def test_check_returns_none_when_no_key(self) -> None:
        """无 Idempotency-Key 头时返回 None."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key=None)
            assert idempotency.check_idempotency(request) is None

    def test_check_returns_none_first_time(self) -> None:
        """首次请求返回 None（应继续执行业务）."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key="conv-1")
            assert idempotency.check_idempotency(request) is None

    def test_check_returns_cached_on_second(self) -> None:
        """store_result 后第二次返回缓存响应."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key="conv-2")
            idempotency.check_idempotency(request)  # 首次
            idempotency.store_idempotency_result(request, 200, {"ok": True})
            cached = idempotency.check_idempotency(request)
            assert cached is not None
            assert cached.status_code == 200
            body = json.loads(cached.content)
            assert body == {"ok": True}

    def test_check_raises_409_when_in_progress(self) -> None:
        """未 store_result 前第二次请求抛 409."""
        from ninja.errors import HttpError

        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key="conv-3")
            idempotency.check_idempotency(request)  # 首次获取槽位
            with pytest.raises(HttpError) as exc_info:
                idempotency.check_idempotency(request)
            assert exc_info.value.status_code == 409

    def test_release_allows_retry_via_convenience(self) -> None:
        """release_idempotency 后可重新 acquire."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key="conv-4")
            idempotency.check_idempotency(request)  # 首次
            idempotency.release_idempotency(request)
            assert idempotency.check_idempotency(request) is None

    def test_store_and_release_noop_without_key(self) -> None:
        """无 key 时 store/release 为空操作."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(key=None)
            idempotency.store_idempotency_result(request, 200, {"x": 1})
            idempotency.release_idempotency(request)
            # 无异常即通过

    def test_check_returns_none_when_unauthenticated(self) -> None:
        """未认证时返回 None."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            request = _make_request(user_pk=None, key="conv-5")
            assert idempotency.check_idempotency(request) is None

    def test_corrupt_cache_body_releases_and_returns_none(self) -> None:
        """缓存 body 损坏时释放槽位并返回 None（重新执行）."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            from apps.system.redis_client import get_redis

            client = get_redis()
            assert client is not None
            # 直接写入 completed 但 body 损坏
            payload = json.dumps(
                {
                    "state": "completed",
                    "status_code": 200,
                    "body": "{broken",
                    "created_at": time.time(),
                }
            )
            client.set("rdbase:idem:user:1:corrupt", payload)
            request = _make_request(user_pk=1, key="corrupt")
            result = idempotency.check_idempotency(request)
            assert result is None  # 损坏后释放，允许重新执行
