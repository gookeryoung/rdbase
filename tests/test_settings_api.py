"""系统设置 API 集成测试.

验证 CRUD 接口、加密密钥轮换、权限控制。
"""

from __future__ import annotations

import json
from typing import Any, Callable, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.models import DataSource
from apps.settings.models import SystemSetting
from django.http import HttpResponse
from django.test import Client


def _auth(user: User) -> dict[str, str]:
    """获取认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _get(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    h = headers or {}
    return cast(HttpResponse, client.get(url, **h))


def _post(
    client: Client,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    h = headers or {}
    if body is None:
        return cast(HttpResponse, client.post(url, **h))
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json", **h),
    )


def _patch(
    client: Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    h = headers or {}
    return cast(
        HttpResponse,
        client.patch(url, data=json.dumps(body), content_type="application/json", **h),
    )


@pytest.mark.django_db
class TestSettingsAPIList:
    """列表接口测试."""

    def test_list_empty(self, client: Client, admin_user: User) -> None:
        """空库应返回空列表."""
        response = _get(client, "/api/v1/settings/settings", _auth(admin_user))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_data(self, client: Client, admin_user: User) -> None:
        """有数据应返回列表."""
        SystemSetting.objects.create(key="session.timeout", value="30", value_type="int")
        SystemSetting.objects.create(key="session.min_length", value="8", value_type="int")
        response = _get(client, "/api/v1/settings/settings", _auth(admin_user))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        keys = [item["key"] for item in data["items"]]
        assert "session.min_length" in keys
        assert "session.timeout" in keys

    def test_list_requires_auth(self, client: Client) -> None:
        """未认证应返回 401."""
        response = _get(client, "/api/v1/settings/settings")
        assert response.status_code == 401

    def test_list_requires_admin(self, client: Client, regular_user: User) -> None:
        """非管理员应返回 403."""
        response = _get(client, "/api/v1/settings/settings", _auth(regular_user))
        assert response.status_code == 403


@pytest.mark.django_db
class TestSettingsAPIUpdate:
    """更新接口测试."""

    def test_update_setting(self, client: Client, admin_user: User) -> None:
        """更新设置项应成功."""
        SystemSetting.objects.create(key="session.timeout", value="15", value_type="int")
        response = _patch(
            client,
            "/api/v1/settings/settings/session.timeout",
            {"value": "30", "description": "更新超时"},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["value"] == "30"
        assert data["description"] == "更新超时"

    def test_update_not_found(self, client: Client, admin_user: User) -> None:
        """更新不存在的设置项应返回 404."""
        response = _patch(
            client,
            "/api/v1/settings/settings/nonexistent",
            {"value": "test"},
            _auth(admin_user),
        )
        assert response.status_code == 404

    def test_update_requires_admin(self, client: Client, regular_user: User) -> None:
        """非管理员无法更新."""
        SystemSetting.objects.create(key="test.key", value="old", value_type="str")
        response = _patch(
            client,
            "/api/v1/settings/settings/test.key",
            {"value": "new"},
            _auth(regular_user),
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestSettingsAPIRotateKey:
    """加密密钥轮换测试."""

    def test_rotate_requires_confirm(self, client: Client, admin_user: User) -> None:
        """未确认应返回 400."""
        response = _post(
            client,
            "/api/v1/settings/rotate-key",
            {"confirm": False},
            _auth(admin_user),
        )
        assert response.status_code == 400

    def test_rotate_no_datasources(self, client: Client, admin_user: User) -> None:
        """无数据源时应返回成功."""
        response = _post(
            client,
            "/api/v1/settings/rotate-key",
            {"confirm": True},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["rotated_count"] == 0

    def test_rotate_with_auto_generated(self, client: Client, admin_user: User, mysql_ds: DataSource) -> None:
        """自动生成密钥轮换应成功."""
        response = _post(
            client,
            "/api/v1/settings/rotate-key",
            {"confirm": True},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["rotated_count"] >= 1

        # 验证数据源密码已重新加密
        mysql_ds.refresh_from_db()
        assert mysql_ds.password_encrypted
        assert mysql_ds.password_encrypted != ""

    def test_rotate_with_custom_key(self, client: Client, admin_user: User, mysql_ds: DataSource) -> None:
        """自定义密钥轮换应成功."""
        import secrets

        new_key = secrets.token_hex(32)
        response = _post(
            client,
            "/api/v1/settings/rotate-key",
            {"confirm": True, "new_key": new_key},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["rotated_count"] >= 1

    def test_rotate_requires_admin(self, client: Client, regular_user: User) -> None:
        """非管理员无法轮换."""
        response = _post(
            client,
            "/api/v1/settings/rotate-key",
            {"confirm": True},
            _auth(regular_user),
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestSettingsAPIPresets:
    """预置项接口测试."""

    def test_list_presets(self, client: Client, admin_user: User) -> None:
        """列出预置项应返回所有定义."""
        response = _get(client, "/api/v1/settings/settings/presets", _auth(admin_user))
        assert response.status_code == 200
        data = response.json()
        keys = [item["key"] for item in data]
        assert "session.access_token_minutes" in keys
        assert "password.min_length" in keys

    def test_init_settings(self, client: Client, admin_user: User) -> None:
        """初始化预置项应成功."""
        response = _post(
            client,
            "/api/v1/settings/settings/init",
            headers=_auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert "detail" in data
        assert SystemSetting.objects.filter(key="session.access_token_minutes").exists()
        assert SystemSetting.objects.filter(key="password.min_length").exists()

    def test_init_idempotent(self, client: Client, admin_user: User) -> None:
        """重复初始化应幂等."""
        _post(
            client,
            "/api/v1/settings/settings/init",
            headers=_auth(admin_user),
        )
        response = _post(
            client,
            "/api/v1/settings/settings/init",
            headers=_auth(admin_user),
        )
        assert response.status_code == 200
        from apps.settings.models import PRESET_SETTINGS

        assert SystemSetting.objects.count() == len(PRESET_SETTINGS)
