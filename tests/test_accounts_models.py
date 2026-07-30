"""accounts 模型测试."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.models import Role, User


@pytest.mark.django_db
def test_user_default_role_is_viewer(make_user: Callable[..., User]) -> None:
    """新建用户默认角色应为 viewer."""
    user = make_user()
    assert user.role == Role.VIEWER


@pytest.mark.django_db
def test_user_is_admin_true_for_admin_role(make_user: Callable[..., User]) -> None:
    """admin 角色用户 is_admin 应为 True."""
    user = make_user(role=Role.ADMIN)
    assert user.is_admin is True


@pytest.mark.django_db
def test_user_is_admin_false_for_viewer_role(make_user: Callable[..., User]) -> None:
    """viewer 角色用户 is_admin 应为 False."""
    user = make_user(role=Role.VIEWER)
    assert user.is_admin is False


@pytest.mark.django_db
def test_user_str_returns_username(make_user: Callable[..., User]) -> None:
    """__str__ 应返回用户名."""
    user = make_user(username="bob")
    assert str(user) == "bob"


@pytest.mark.django_db
def test_user_check_password_verifies(make_user: Callable[..., User]) -> None:
    """set_password 后 check_password 应验证正确密码通过、错误密码失败."""
    user = make_user(password="secret123")
    assert user.check_password("secret123") is True
    assert user.check_password("wrong") is False


@pytest.mark.django_db
def test_role_choices_cover_three_roles() -> None:
    """角色枚举应包含 admin/designer/viewer 三种."""
    roles = {choice[0] for choice in Role.choices}
    assert roles == {"admin", "designer", "viewer"}
