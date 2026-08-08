"""pytest 共享 fixture."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.models import DataSource, EngineType
from apps.system import circuit_breaker, distributed_lock, idempotency, redis_client
from django.test import Client


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> Any:
    """每个测试前后重置熔断器后端与 breaker 缓存，避免跨测试失败计数污染.

    同时重置 Redis 客户端单例，使 breaker 后端按当前 settings 重新解析
    （测试可能用 override_settings 切换 REDIS_FAKE）。
    fakeredis 同 URL 跨实例共享 server 数据，``reset_redis_client`` 仅清单例
    不清 server 数据，故需显式 ``flush_redis`` 避免跨测试残留污染。
    """
    redis_client.flush_redis()
    redis_client.reset_redis_client()
    circuit_breaker.reset_backend()
    yield
    redis_client.flush_redis()
    redis_client.reset_redis_client()
    circuit_breaker.reset_backend()


@pytest.fixture(autouse=True)
def _reset_idempotency_and_lock() -> Any:
    """每个测试前后重置幂等存储与分布式锁后端单例.

    与 _reset_circuit_breaker 协同：先重置 redis_client，再重置依赖它的
    idempotency/lock 后端，确保跨测试不残留缓存与锁占用。
    """
    idempotency.reset_store()
    idempotency.reset_manager()
    distributed_lock.reset_backend()
    yield
    idempotency.reset_store()
    idempotency.reset_manager()
    distributed_lock.reset_backend()


@pytest.fixture(autouse=True)
def _noop_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 sync_service 的退避 sleep 替换为空操作，避免重试测试真实等待."""

    def _noop(_delay: float) -> None:
        return None

    from apps.sync import sync_service

    monkeypatch.setattr(sync_service, "_backoff_sleep", _noop)


@pytest.fixture
def make_user(db: Any) -> Callable[..., User]:
    """用户工厂：按需创建不同角色用户."""

    def _factory(
        username: str = "alice",
        password: str = "pass1234",
        role: str = Role.VIEWER,
        email: str = "",
        is_active: bool = True,
    ) -> User:
        """创建并返回用户."""
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            role=role,
        )
        if not is_active:
            user.is_active = False
            user.save()
        return user

    return _factory


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    """管理员用户."""
    return make_user(username="admin", role=Role.ADMIN)


@pytest.fixture
def regular_user(make_user: Callable[..., User]) -> User:
    """普通用户（viewer 角色）."""
    return make_user(username="viewer_user", role=Role.VIEWER)


@pytest.fixture
def designer_user(make_user: Callable[..., User]) -> User:
    """设计师用户."""
    return make_user(username="designer", role=Role.DESIGNER)


@pytest.fixture
def auth_client(client: Client) -> Callable[..., Client]:
    """带认证的客户端工厂."""

    def _factory(user: User) -> Client:
        token = create_access_token(user.pk, str(user.role))
        c = Client()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return c

    return _factory


@pytest.fixture
def mysql_ds(db: Any, admin_user: User) -> DataSource:
    """MySQL 数据源 fixture（带加密密码）."""
    from apps.datasources.crypto import encrypt_password
    from django.conf import settings

    plaintext = "secret_password_123"
    encrypted = encrypt_password(plaintext, settings.SECRET_KEY)
    return DataSource.objects.create(
        name="test_mysql",
        engine=EngineType.MYSQL,
        host="localhost",
        port=3306,
        database="testdb",
        username="root",
        group="test",
        password_encrypted=encrypted,
        created_by=admin_user,
    )
