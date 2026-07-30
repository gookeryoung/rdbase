"""用户管理接口测试（admin only）与个人改密测试."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from django.http import HttpResponse
from django.test import Client


def _post(
    client: Client,
    url: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 POST 请求."""
    h = headers or {}
    if body is None:
        return cast(HttpResponse, client.post(url, **h))
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _get(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """发送 GET 请求."""
    h = headers or {}
    return cast(HttpResponse, client.get(url, **h))


def _patch(
    client: Client,
    url: str,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """发送 PATCH 请求."""
    h = headers or {}
    return cast(
        HttpResponse,
        client.patch(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _auth_header(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# ---------- 用户列表 ----------


@pytest.mark.django_db
def test_list_users_as_admin_returns_list(make_user: Callable[..., User]) -> None:
    """管理员应能获取用户列表."""
    admin = make_user(username="admin", role=Role.ADMIN)
    make_user(username="alice", role=Role.VIEWER)
    make_user(username="bob", role=Role.DESIGNER)
    client = Client()
    response = _get(client, "/api/v1/users", headers=_auth_header(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert isinstance(body, list)
    assert len(body) == 3
    usernames = [u["username"] for u in body]
    assert {"admin", "alice", "bob"} == set(usernames)


@pytest.mark.django_db
def test_list_users_as_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 访问用户列表应返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    client = Client()
    response = _get(client, "/api/v1/users", headers=_auth_header(viewer))
    assert response.status_code == 403


@pytest.mark.django_db
def test_list_users_as_designer_returns_403(make_user: Callable[..., User]) -> None:
    """designer 访问用户列表应返回 403."""
    designer = make_user(username="designer", role=Role.DESIGNER)
    client = Client()
    response = _get(client, "/api/v1/users", headers=_auth_header(designer))
    assert response.status_code == 403


@pytest.mark.django_db
def test_list_users_without_token_returns_401() -> None:
    """未认证访问用户列表应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/users")
    assert response.status_code == 401


# ---------- 切换启用/禁用 ----------


@pytest.mark.django_db
def test_toggle_active_disables_user(make_user: Callable[..., User]) -> None:
    """管理员应能禁用启用中的用户."""
    admin = make_user(username="admin", role=Role.ADMIN)
    target = make_user(username="alice", is_active=True)
    client = Client()
    response = _post(client, f"/api/v1/users/{target.pk}/toggle-active", headers=_auth_header(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["is_active"] is False
    target.refresh_from_db()
    assert target.is_active is False


@pytest.mark.django_db
def test_toggle_active_enables_user(make_user: Callable[..., User]) -> None:
    """管理员应能启用已禁用的用户."""
    admin = make_user(username="admin", role=Role.ADMIN)
    target = make_user(username="alice", is_active=False)
    client = Client()
    response = _post(client, f"/api/v1/users/{target.pk}/toggle-active", headers=_auth_header(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["is_active"] is True


@pytest.mark.django_db
def test_toggle_active_unknown_user_returns_404(make_user: Callable[..., User]) -> None:
    """切换不存在用户的状态应返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = Client()
    response = _post(client, "/api/v1/users/99999/toggle-active", headers=_auth_header(admin))
    assert response.status_code == 404


@pytest.mark.django_db
def test_toggle_active_as_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 切换用户状态应返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    target = make_user(username="alice")
    client = Client()
    response = _post(client, f"/api/v1/users/{target.pk}/toggle-active", headers=_auth_header(viewer))
    assert response.status_code == 403


# ---------- 重置密码 ----------


@pytest.mark.django_db
def test_reset_password_succeeds(make_user: Callable[..., User]) -> None:
    """管理员重置密码后，用户应能用新密码登录."""
    admin = make_user(username="admin", role=Role.ADMIN)
    target = make_user(username="alice", password="old-pass")
    client = Client()
    response = _post(
        client,
        f"/api/v1/users/{target.pk}/reset-password",
        {"new_password": "new-pass-123"},
        headers=_auth_header(admin),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["detail"] == "密码已重置"
    target.refresh_from_db()
    assert target.check_password("new-pass-123") is True
    assert target.check_password("old-pass") is False


@pytest.mark.django_db
def test_reset_password_unknown_user_returns_404(make_user: Callable[..., User]) -> None:
    """重置不存在用户的密码应返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = Client()
    response = _post(
        client,
        "/api/v1/users/99999/reset-password",
        {"new_password": "new-pass"},
        headers=_auth_header(admin),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_reset_password_as_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 重置密码应返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    target = make_user(username="alice")
    client = Client()
    response = _post(
        client,
        f"/api/v1/users/{target.pk}/reset-password",
        {"new_password": "new-pass"},
        headers=_auth_header(viewer),
    )
    assert response.status_code == 403


# ---------- 修改角色 ----------


@pytest.mark.django_db
def test_update_role_succeeds(make_user: Callable[..., User]) -> None:
    """管理员应能修改用户角色."""
    admin = make_user(username="admin", role=Role.ADMIN)
    target = make_user(username="alice", role=Role.VIEWER)
    client = Client()
    response = _patch(
        client,
        f"/api/v1/users/{target.pk}/role",
        {"role": Role.DESIGNER},
        headers=_auth_header(admin),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["role"] == Role.DESIGNER
    target.refresh_from_db()
    assert target.role == Role.DESIGNER


@pytest.mark.django_db
def test_update_role_invalid_returns_400(make_user: Callable[..., User]) -> None:
    """无效角色应返回 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    target = make_user(username="alice")
    client = Client()
    response = _patch(
        client,
        f"/api/v1/users/{target.pk}/role",
        {"role": "superuser"},
        headers=_auth_header(admin),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_update_role_unknown_user_returns_404(make_user: Callable[..., User]) -> None:
    """修改不存在用户的角色应返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = Client()
    response = _patch(
        client,
        "/api/v1/users/99999/role",
        {"role": Role.ADMIN},
        headers=_auth_header(admin),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_update_role_as_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 修改角色应返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    target = make_user(username="alice")
    client = Client()
    response = _patch(
        client,
        f"/api/v1/users/{target.pk}/role",
        {"role": Role.ADMIN},
        headers=_auth_header(viewer),
    )
    assert response.status_code == 403


# ---------- 个人改密 ----------


@pytest.mark.django_db
def test_change_password_succeeds(make_user: Callable[..., User]) -> None:
    """用户用正确旧密码应能修改密码."""
    user = make_user(username="alice", password="old-pass")
    client = Client()
    response = _post(
        client,
        "/api/v1/auth/change-password",
        {"old_password": "old-pass", "new_password": "new-pass-123"},
        headers=_auth_header(user),
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["detail"] == "密码已修改"
    user.refresh_from_db()
    assert user.check_password("new-pass-123") is True


@pytest.mark.django_db
def test_change_password_wrong_old_returns_400(make_user: Callable[..., User]) -> None:
    """旧密码错误应返回 400."""
    user = make_user(username="alice", password="old-pass")
    client = Client()
    response = _post(
        client,
        "/api/v1/auth/change-password",
        {"old_password": "wrong", "new_password": "new-pass"},
        headers=_auth_header(user),
    )
    assert response.status_code == 400
    body = json.loads(response.content)
    assert "旧密码" in body["detail"]


@pytest.mark.django_db
def test_change_password_without_token_returns_401() -> None:
    """未认证改密应返回 401."""
    client = Client()
    response = _post(
        client,
        "/api/v1/auth/change-password",
        {"old_password": "x", "new_password": "y"},
    )
    assert response.status_code == 401
