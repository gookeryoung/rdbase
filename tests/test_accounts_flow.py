"""accounts 全链路集成测试.

覆盖注册 → 登录 → /me → 改密 → 刷新 → 登出的端到端流程，
验证 access token 与 refresh cookie 在多次请求间的协作。
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from django.http import HttpResponse
from django.test import Client


def _post(client: Client, url: str, body: dict[str, object] | None = None) -> HttpResponse:
    """发送 POST 请求（body 为 None 时不带 JSON body）."""
    if body is None:
        return cast(HttpResponse, client.post(url))
    return cast(HttpResponse, client.post(url, data=json.dumps(body), content_type="application/json"))


def _get(client: Client, url: str, token: str | None = None) -> HttpResponse:
    """发送 GET 请求，可选附加 Bearer token."""
    headers: dict[str, str] = {}
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return cast(HttpResponse, client.get(url, **headers))


@pytest.mark.django_db
def test_full_auth_flow_register_login_me_change_refresh_logout() -> None:
    """注册→登录→me→改密→刷新→登出 全链路应正常协作."""
    client = Client()

    # 1. 注册新用户
    response = _post(
        client,
        "/api/v1/auth/register",
        {"username": "flowuser", "password": "pass1234", "email": "flow@example.com"},
    )
    assert response.status_code == 201
    register_body = json.loads(response.content)
    assert register_body["user"]["username"] == "flowuser"
    # 注册响应应同时签发 access 与 refresh cookie
    assert "access" in register_body
    assert "refresh_token" in response.cookies

    # 2. 登录（用同一 client 复用 cookie，但这里验证登录会重新签发）
    response = _post(
        client,
        "/api/v1/auth/login",
        {"username": "flowuser", "password": "pass1234"},
    )
    assert response.status_code == 200
    login_body = json.loads(response.content)
    access = login_body["access"]
    assert login_body["user"]["username"] == "flowuser"
    assert "refresh_token" in response.cookies

    # 3. 用 access token 访问 /me，应返回当前用户
    response = _get(client, "/api/v1/auth/me", token=access)
    assert response.status_code == 200
    me_body = json.loads(response.content)
    assert me_body["username"] == "flowuser"
    assert me_body["email"] == "flow@example.com"

    # 4. 修改密码
    response = _post(
        client,
        "/api/v1/auth/change-password",
        {"old_password": "pass1234", "new_password": "newpass1234"},
    )
    # 改密接口需带 token
    assert response.status_code == 401

    # 改密需带 Authorization 头，Client.get 不支持 POST+header，用 post 的 headers 参数
    response = cast(
        HttpResponse,
        client.post(
            "/api/v1/auth/change-password",
            data=json.dumps({"old_password": "pass1234", "new_password": "newpass1234"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        ),
    )
    assert response.status_code == 200
    assert json.loads(response.content)["detail"] == "密码已修改"

    # 5. 用 refresh cookie 换发新 access token
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 200
    refreshed = json.loads(response.content)
    assert "access" in refreshed
    # 新 access 应能继续访问 /me
    response = _get(client, "/api/v1/auth/me", token=refreshed["access"])
    assert response.status_code == 200

    # 6. 登出：清除 refresh cookie
    response = _post(client, "/api/v1/auth/logout")
    assert response.status_code == 200
    cookie = response.cookies.get("refresh_token")
    assert cookie is not None
    assert cookie.value == ""

    # 登出后再刷新应失败（cookie 已清空）
    response = _post(client, "/api/v1/auth/refresh")
    assert response.status_code == 401


@pytest.mark.django_db
def test_login_with_new_password_after_change() -> None:
    """改密后用新密码登录应成功，旧密码应失败."""
    client = Client()

    # 注册并登录拿 access
    _post(
        client,
        "/api/v1/auth/register",
        {"username": "pwduser", "password": "old1234"},
    )
    response = _post(client, "/api/v1/auth/login", {"username": "pwduser", "password": "old1234"})
    access = json.loads(response.content)["access"]

    # 改密
    response = cast(
        HttpResponse,
        client.post(
            "/api/v1/auth/change-password",
            data=json.dumps({"old_password": "old1234", "new_password": "new1234"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        ),
    )
    assert response.status_code == 200

    # 新密码登录应成功
    response = _post(client, "/api/v1/auth/login", {"username": "pwduser", "password": "new1234"})
    assert response.status_code == 200

    # 旧密码登录应失败
    response = _post(client, "/api/v1/auth/login", {"username": "pwduser", "password": "old1234"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_change_password_wrong_old_returns_400() -> None:
    """改密时旧密码错误应返回 400."""
    client = Client()
    _post(client, "/api/v1/auth/register", {"username": "u", "password": "p1234"})
    response = _post(client, "/api/v1/auth/login", {"username": "u", "password": "p1234"})
    access = json.loads(response.content)["access"]

    response = cast(
        HttpResponse,
        client.post(
            "/api/v1/auth/change-password",
            data=json.dumps({"old_password": "wrong", "new_password": "new1234"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        ),
    )
    assert response.status_code == 400
