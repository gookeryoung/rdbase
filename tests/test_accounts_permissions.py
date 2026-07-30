"""RBAC 权限依赖测试."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from apps.accounts.models import Role, User
from apps.accounts.permissions import (
    require_admin,
    require_designer_or_admin,
    require_roles,
)
from ninja.errors import HttpError


def _make_request(user: User | None) -> SimpleNamespace:
    """构造模拟 request：仅含 auth 属性."""
    return SimpleNamespace(auth=user)


def test_require_roles_rejects_empty_roles() -> None:
    """未指定允许角色应抛 ValueError."""
    with pytest.raises(ValueError, match="至少需要指定一个允许角色"):
        require_roles()


@pytest.mark.django_db
def test_require_roles_allows_authorized_user(make_user: Callable[..., User]) -> None:
    """角色在允许列表中的用户应通过校验."""
    user = make_user(username="admin", role=Role.ADMIN)
    dep = require_roles(Role.ADMIN)
    # 不抛异常即通过
    dep(_make_request(user))


@pytest.mark.django_db
def test_require_roles_denies_unauthorized_user(make_user: Callable[..., User]) -> None:
    """角色不在允许列表中应抛 403."""
    user = make_user(username="viewer", role=Role.VIEWER)
    dep = require_roles(Role.ADMIN)
    with pytest.raises(HttpError) as exc_info:
        dep(_make_request(user))
    assert exc_info.value.status_code == 403


def test_require_roles_denies_unauthenticated() -> None:
    """未认证（request.auth 为 None）应抛 401."""
    dep = require_roles(Role.ADMIN)
    with pytest.raises(HttpError) as exc_info:
        dep(_make_request(None))
    assert exc_info.value.status_code == 401


def test_require_roles_denies_non_user_auth() -> None:
    """request.auth 非 User 实例应抛 401."""
    dep = require_roles(Role.ADMIN)
    with pytest.raises(HttpError) as exc_info:
        dep(_make_request("not-a-user"))  # type: ignore[arg-type]
    assert exc_info.value.status_code == 401


@pytest.mark.django_db
def test_require_admin_allows_admin(make_user: Callable[..., User]) -> None:
    """预构造 require_admin 应放行 admin."""
    user = make_user(username="admin", role=Role.ADMIN)
    require_admin(_make_request(user))


@pytest.mark.django_db
def test_require_admin_denies_viewer(make_user: Callable[..., User]) -> None:
    """预构造 require_admin 应拒绝 viewer."""
    user = make_user(username="viewer", role=Role.VIEWER)
    with pytest.raises(HttpError) as exc_info:
        require_admin(_make_request(user))
    assert exc_info.value.status_code == 403


@pytest.mark.django_db
def test_require_designer_or_admin_allows_designer(make_user: Callable[..., User]) -> None:
    """require_designer_or_admin 应放行 designer."""
    user = make_user(username="designer", role=Role.DESIGNER)
    require_designer_or_admin(_make_request(user))


@pytest.mark.django_db
def test_require_designer_or_admin_allows_admin(make_user: Callable[..., User]) -> None:
    """require_designer_or_admin 应放行 admin."""
    user = make_user(username="admin", role=Role.ADMIN)
    require_designer_or_admin(_make_request(user))


@pytest.mark.django_db
def test_require_designer_or_admin_denies_viewer(make_user: Callable[..., User]) -> None:
    """require_designer_or_admin 应拒绝 viewer."""
    user = make_user(username="viewer", role=Role.VIEWER)
    with pytest.raises(HttpError) as exc_info:
        require_designer_or_admin(_make_request(user))
    assert exc_info.value.status_code == 403


def test_require_roles_returns_callable() -> None:
    """工厂返回值应为可调用对象."""
    dep = require_roles(Role.VIEWER)
    assert callable(dep)


def test_require_roles_each_call_returns_new_instance() -> None:
    """工厂每次调用应返回新的闭包实例（避免共享状态）."""
    dep1 = require_roles(Role.ADMIN)
    dep2 = require_roles(Role.ADMIN)
    assert dep1 is not dep2
