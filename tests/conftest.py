"""pytest 共享 fixture."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from apps.accounts.models import Role, User


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
