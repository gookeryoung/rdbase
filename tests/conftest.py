"""pytest 共享 fixture."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.datasources.models import DataSource, EngineType
from django.test import Client


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
