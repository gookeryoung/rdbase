"""accounts API 接口测试."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token, create_refresh_token
from apps.accounts.models import Role, User
from django.http import HttpResponse
from django.test import Client


def _post(client: Client, url: str, body: dict[str, object] | None = None) -> HttpResponse:
    """发送 POST 请求（body 为 None 时不带 JSON body）."""
    if body is None:
        return cast(HttpResponse, client.post(url))
    return cast(HttpResponse, client.post(url, data=json.dumps(body), content_type="application/json"))


def _get(client: Client, url: str, **headers: str) -> HttpResponse:
    """发送 GET 请求."""
    return cast(HttpResponse, client.get(url, **headers))


@pytest.mark.django_db
def test_register_creates_user_and_sets_cookie() -> None:
    """注册应创建用户、返回 access token 并设置 refresh cookie."""
    client = Client()
    response = _post(
        client,
        "/api/v1/auth/register",
        {"username": "newuser", "password": "pass1234", "email": "new@example.com"},
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert "access" in body
    assert body["user"]["username"] == "newuser"
    assert body["user"]["role"] == Role.VIEWER
    assert "refresh_token" in response.cookies
    assert User.objects.filter(username="newuser").exists()


@pytest.mark.django_db
def test_register_duplicate_username_returns_400(make_user: Callable[..., User]) -> None:
    """重复用户名注册应返回 400."""
    make_user(username="dup")
    client = Client()
    response = _post(client, "/api/v1/auth/register", {"username": "dup", "password": "pass1234"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_login_success_returns_access_and_sets_cookie(make_user: Callable[..., User]) -> None:
    """正确凭据登录应返回 access token 并设置 refresh cookie."""
    make_user(username="alice", password="pass1234", role=Role.ADMIN)
    client = Client()
    response = _post(client, "/api/v1/auth/login", {"username": "alice", "password": "pass1234"})
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "access" in body
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == Role.ADMIN
    assert "refresh_token" in response.cookies


@pytest.mark.django_db
def test_login_wrong_password_returns_401(make_user: Callable[..., User]) -> None:
    """错误密码登录应返回 401."""
    make_user(username="alice", password="pass1234")
    client = Client()
    response = _post(client, "/api/v1/auth/login", {"username": "alice", "password": "wrong"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_login_inactive_user_returns_401(make_user: Callable[..., User]) -> None:
    """禁用用户登录应返回 401."""
    make_user(username="bob", password="pass1234", is_active=False)
    client = Client()
    response = _post(client, "/api/v1/auth/login", {"username": "bob", "password": "pass1234"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_clears_refresh_cookie() -> None:
    """登出应清除 refresh token cookie."""
    client = Client()
    response = _post(client, "/api/v1/auth/logout")
    assert response.status_code == 200
    cookie = response.cookies.get("refresh_token")
    assert cookie is not None
    assert cookie.value == ""


@pytest.mark.django_db
def test_refresh_with_valid_cookie_returns_new_access(make_user: Callable[..., User]) -> None:
    """有效 refresh cookie 应换发新 access token."""
    user = make_user(username="alice", role=Role.DESIGNER)
    client = Client()
    client.cookies["refresh_token"] = create_refresh_token(user.pk)
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "access" in body


@pytest.mark.django_db
def test_refresh_without_cookie_returns_401() -> None:
    """无 refresh cookie 应返回 401."""
    client = Client()
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_with_invalid_cookie_returns_401() -> None:
    """无效 refresh cookie 应返回 401."""
    client = Client()
    client.cookies["refresh_token"] = "invalid.token.value"
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_with_access_token_type_returns_401(make_user: Callable[..., User]) -> None:
    """refresh 接口收到 access 类型 token 应返回 401."""
    user = make_user(username="alice")
    client = Client()
    client.cookies["refresh_token"] = create_access_token(user.pk, str(user.role))
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_with_inactive_user_returns_401(make_user: Callable[..., User]) -> None:
    """用户禁用后 refresh 应返回 401."""
    user = make_user(username="bob", is_active=False)
    client = Client()
    client.cookies["refresh_token"] = create_refresh_token(user.pk)
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_with_unknown_user_returns_401(make_user: Callable[..., User]) -> None:
    """refresh token 对应用户不存在应返回 401."""
    make_user(username="alice")
    client = Client()
    client.cookies["refresh_token"] = create_refresh_token(99999)
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_valid_token_returns_user(make_user: Callable[..., User]) -> None:
    """带有效 access token 访问 /me 应返回用户信息."""
    user = make_user(username="alice", role=Role.ADMIN)
    token = create_access_token(user.pk, str(user.role))
    client = Client()
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["username"] == "alice"
    assert body["role"] == Role.ADMIN


@pytest.mark.django_db
def test_me_without_token_returns_401() -> None:
    """无 token 访问 /me 应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_invalid_token_returns_401() -> None:
    """无效 token 访问 /me 应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION="Bearer invalid.token")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_inactive_user_returns_401(make_user: Callable[..., User]) -> None:
    """用户被禁用后 access token 应失效."""
    user = make_user(username="bob", is_active=False)
    token = create_access_token(user.pk, str(user.role))
    client = Client()
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_refresh_token_returns_401(make_user: Callable[..., User]) -> None:
    """用 refresh token 访问 /me 应返回 401（token 类型错误）."""
    user = make_user(username="alice")
    client = Client()
    token = create_refresh_token(user.pk)
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_unknown_user_returns_401(make_user: Callable[..., User]) -> None:
    """token user_id 对应用户不存在应返回 401."""
    make_user(username="alice")
    client = Client()
    token = create_access_token(99999, "viewer")
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_with_non_int_user_id_returns_401() -> None:
    """token user_id 非 int 应返回 401."""
    from datetime import datetime, timedelta, timezone

    import jwt as jwt_mod
    from django.conf import settings

    now = datetime.now(timezone.utc)
    payload = {
        "user_id": "not-int",
        "token_type": "access",
        "exp": now + timedelta(hours=1),
        "iat": now,
    }
    key = settings.SECRET_KEY
    assert key is not None
    token = jwt_mod.encode(payload, key, algorithm="HS256")
    client = Client()
    response = _get(client, "/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401


@pytest.mark.django_db
def test_admin_registers_user_model() -> None:
    """accounts.User 应注册到 admin.site."""
    from django.contrib import admin

    assert User in admin.site._registry
