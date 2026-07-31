"""端到端业务流程集成测试.

验证从登录到核心业务操作的完整用户旅程：
1. 用户认证（登录 → 获取 JWT → 访问受保护接口）
2. 数据源管理（创建 → 查询列表 → 查看详情）
3. 系统设置（读取 → 更新）
4. 审计日志（验证操作被记录）
5. RBAC 权限（viewer 角色被拒绝管理操作）
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.audit.models import AuditLog
from django.http import HttpResponse
from django.test import Client


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


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


def _get(
    client: Client,
    url: str,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
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


@pytest.mark.django_db
class TestE2EUserJourney:
    """端到端用户旅程测试."""

    def test_full_admin_journey(self, client: Client, admin_user: User) -> None:
        """管理员完整旅程：健康检查 → 创建数据源 → 查看审计日志 → 读取系统设置."""
        headers = _auth(admin_user)

        # 1. 健康检查
        resp = _get(client, "/health/")
        assert resp.status_code == 200

        # 2. 创建数据源
        resp = _post(
            client,
            "/api/v1/datasources",
            {
                "name": "e2e-test-sqlite",
                "engine": "sqlite",
                "database": ":memory:",
                "username": "",
            },
            headers,
        )
        assert resp.status_code == 201
        ds_data = resp.json()
        ds_id = ds_data["id"]
        assert ds_data["name"] == "e2e-test-sqlite"

        # 3. 查询数据源列表
        resp = _get(client, "/api/v1/datasources", headers)
        assert resp.status_code == 200
        ds_list = resp.json()
        assert any(d["id"] == ds_id for d in ds_list)

        # 4. 查看数据源详情
        resp = _get(client, f"/api/v1/datasources/{ds_id}", headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "e2e-test-sqlite"

        # 5. 读取系统设置
        resp = _get(client, "/api/v1/settings/settings", headers)
        assert resp.status_code == 200

        # 6. 查看审计日志（验证前面的操作被记录）
        resp = _get(client, "/api/v1/audit/logs?limit=20", headers)
        assert resp.status_code == 200
        logs_data = resp.json()
        assert logs_data["total"] > 0

    def test_rbac_viewer_denied(self, client: Client, admin_user: User, regular_user: User) -> None:
        """viewer 角色不能执行管理操作."""
        admin_headers = _auth(admin_user)
        viewer_headers = _auth(regular_user)

        # 管理员创建数据源
        resp = _post(
            client,
            "/api/v1/datasources",
            {
                "name": "rbac-test-sqlite",
                "engine": "sqlite",
                "database": ":memory:",
                "username": "",
            },
            admin_headers,
        )
        assert resp.status_code == 201
        ds_id = resp.json()["id"]

        # viewer 可以查看数据源列表
        resp = _get(client, "/api/v1/datasources", viewer_headers)
        assert resp.status_code == 200

        # viewer 不能删除数据源
        resp = client.delete(f"/api/v1/datasources/{ds_id}", **viewer_headers)
        assert resp.status_code in (403, 401)

        # 管理员可以删除
        resp = client.delete(f"/api/v1/datasources/{ds_id}", **admin_headers)
        assert resp.status_code in (200, 204)

    def test_unauthenticated_access_denied(self, client: Client) -> None:
        """未认证用户不能访问受保护接口."""
        # 无 Authorization 头访问数据源列表
        resp = _get(client, "/api/v1/datasources")
        assert resp.status_code == 401

        # 无 Authorization 头访问系统设置
        resp = _get(client, "/api/v1/settings/settings")
        assert resp.status_code == 401

    def test_audit_log_recorded_on_datasource_create(self, client: Client, admin_user: User) -> None:
        """创建数据源操作应被审计日志记录."""
        headers = _auth(admin_user)

        # 记录操作前的审计日志数量
        before_count = AuditLog.objects.count()

        # 创建数据源
        resp = _post(
            client,
            "/api/v1/datasources",
            {
                "name": "audit-test-sqlite",
                "engine": "sqlite",
                "database": ":memory:",
                "username": "",
            },
            headers,
        )
        assert resp.status_code == 201

        # 验证审计日志增加
        after_count = AuditLog.objects.count()
        assert after_count > before_count

    def test_system_settings_read_and_update(self, client: Client, admin_user: User) -> None:
        """系统设置读取与更新流程."""
        headers = _auth(admin_user)

        # 初始化预设设置
        resp = _post(client, "/api/v1/settings/settings/init", None, headers)
        assert resp.status_code == 200

        # 读取设置
        resp = _get(client, "/api/v1/settings/settings", headers)
        assert resp.status_code == 200
        settings_data = resp.json()
        settings_list = settings_data["items"]
        assert len(settings_list) > 0

        # 找到 session.access_token_minutes 设置
        jwt_setting = next(
            (s for s in settings_list if s["key"] == "session.access_token_minutes"),
            None,
        )
        assert jwt_setting is not None

        # 更新设置（使用 key 而非 id）
        resp = _patch(
            client,
            "/api/v1/settings/settings/session.access_token_minutes",
            {"value": "30"},
            headers,
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "30"

    def test_sync_config_full_lifecycle(self, client: Client, admin_user: User) -> None:
        """同步配置完整生命周期：创建 → 查询 → 更新调度 → 删除."""
        headers = _auth(admin_user)

        # 先创建数据源
        resp = _post(
            client,
            "/api/v1/datasources",
            {
                "name": "sync-e2e-sqlite",
                "engine": "sqlite",
                "database": ":memory:",
                "username": "",
            },
            headers,
        )
        assert resp.status_code == 201
        ds_id = resp.json()["id"]

        # 创建同步配置
        resp = _post(
            client,
            "/api/v1/sync/configs",
            {
                "name": "e2e-sync-config",
                "description": "端到端测试同步配置",
                "source_table": "auth_user",
                "source_db_alias": "default",
                "target_datasource_id": ds_id,
                "target_table": "ext_users",
                "sync_mode": "full",
                "field_mappings": [
                    {
                        "source_field": "id",
                        "target_field": "ext_id",
                        "mapping_type": "direct",
                        "is_pk": True,
                    },
                    {
                        "source_field": "username",
                        "target_field": "ext_username",
                        "mapping_type": "direct",
                        "is_pk": False,
                    },
                ],
            },
            headers,
        )
        assert resp.status_code == 200
        config_id = resp.json()["id"]

        # 查询同步配置列表
        resp = _get(client, "/api/v1/sync/configs", headers)
        assert resp.status_code == 200
        assert any(c["id"] == config_id for c in resp.json()["items"])

        # 更新调度设置
        resp = _post(
            client,
            f"/api/v1/sync/configs/{config_id}/schedule",
            {
                "scheduler_enabled": True,
                "cron_expression": "*/10 * * * *",
                "max_retries": 5,
            },
            headers,
        )
        assert resp.status_code == 200
        schedule_data = resp.json()
        assert schedule_data["scheduler_enabled"] is True
        assert schedule_data["cron_expression"] == "*/10 * * * *"
        assert schedule_data["max_retries"] == 5

        # 预览同步数据
        resp = _post(
            client,
            f"/api/v1/sync/configs/{config_id}/preview",
            {"force_full": False},
            headers,
        )
        assert resp.status_code == 200
        preview_data = resp.json()
        assert preview_data["config_id"] == config_id
        assert "target_fields" in preview_data

        # 批量触发
        resp = _post(
            client,
            "/api/v1/sync/batch-trigger",
            {
                "config_ids": [config_id],
                "force_full": True,
                "stop_on_error": False,
                "confirm": True,
            },
            headers,
        )
        assert resp.status_code == 200
        batch_data = resp.json()
        assert batch_data["total"] == 1

        # 查看同步日志
        resp = _get(
            client,
            f"/api/v1/sync/logs?config_id={config_id}&limit=10",
            headers,
        )
        assert resp.status_code == 200

        # 删除同步配置
        resp = client.delete(f"/api/v1/sync/configs/{config_id}", **headers)
        assert resp.status_code in (200, 204)

        # 验证已删除
        resp = _get(client, f"/api/v1/sync/configs/{config_id}", headers)
        assert resp.status_code == 404
