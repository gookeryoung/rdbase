"""同步 API 端点测试."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import SyncConfig, SyncFieldMapping, SyncLog, SyncMode, SyncStatus
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
        client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **h,
        ),
    )


def _patch(
    client: Client,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    h = headers or {}
    if body is None:
        return cast(HttpResponse, client.patch(url, **h))
    return cast(
        HttpResponse,
        client.patch(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **h,
        ),
    )


def _delete(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    h = headers or {}
    return cast(HttpResponse, client.delete(url, **h))


@pytest.fixture
def sync_ds_for_api(db: Any, admin_user: User) -> DataSource:
    """API 测试用数据源."""
    from apps.datasources.crypto import encrypt_password
    from django.conf import settings

    encrypted = encrypt_password("api_test_pwd", settings.SECRET_KEY)
    return DataSource.objects.create(
        name="api_target_sqlite",
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=":memory:",
        username="",
        group="api_sync",
        password_encrypted=encrypted,
        created_by=admin_user,
    )


@pytest.fixture
def sync_config_for_api(db: Any, admin_user: User, sync_ds_for_api: DataSource) -> SyncConfig:
    """API 测试用同步配置."""
    config = SyncConfig.objects.create(
        name="API 测试配置",
        description="API 测试用",
        source_table="auth_user",
        target_datasource=sync_ds_for_api,
        target_table="ext_user",
        sync_mode=SyncMode.INCREMENTAL,
        status=SyncStatus.ACTIVE,
        created_by=admin_user,
    )
    SyncFieldMapping.objects.create(
        config=config,
        source_field="id",
        target_field="ext_id",
        is_pk=True,
    )
    SyncFieldMapping.objects.create(
        config=config,
        source_field="username",
        target_field="ext_name",
        is_pk=False,
    )
    return config


class TestSyncConfigAPI:
    """同步配置 CRUD API 测试."""

    def test_list_configs(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """管理员应能列出所有同步配置."""
        response = _get(client, "/api/v1/sync/configs", _auth(admin_user))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any(c["name"] == sync_config_for_api.name for c in data["items"])

    def test_list_configs_requires_admin(self, client: Client, regular_user: User) -> None:
        """非管理员访问应被拒绝."""
        response = _get(client, "/api/v1/sync/configs", _auth(regular_user))
        assert response.status_code in {401, 403}

    def test_create_config(self, client: Client, admin_user: User, sync_ds_for_api: DataSource) -> None:
        """管理员应能创建同步配置."""
        payload = {
            "name": "新建同步配置",
            "description": "测试创建",
            "source_table": "auth_permission",
            "target_datasource_id": sync_ds_for_api.pk,
            "target_table": "ext_permission",
            "sync_mode": "full",
            "field_mappings": [
                {
                    "source_field": "id",
                    "target_field": "ext_id",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": True,
                }
            ],
        }
        response = _post(client, "/api/v1/sync/configs", payload, _auth(admin_user))
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "新建同步配置"
        assert data["sync_mode"] == "full"
        assert len(data["field_mappings"]) == 1

    def test_create_config_missing_datasource(self, client: Client, admin_user: User) -> None:
        """引用不存在的数据源应返回 404."""
        payload = {
            "name": "无效配置",
            "source_table": "test",
            "target_datasource_id": 99999,
            "target_table": "test",
            "field_mappings": [],
        }
        response = _post(client, "/api/v1/sync/configs", payload, _auth(admin_user))
        assert response.status_code == 404

    def test_get_config(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """管理员应能获取单个配置."""
        response = _get(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}",
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sync_config_for_api.pk
        assert data["name"] == sync_config_for_api.name

    def test_get_config_not_found(self, client: Client, admin_user: User) -> None:
        """获取不存在的配置应返回 404."""
        response = _get(client, "/api/v1/sync/configs/99999", _auth(admin_user))
        assert response.status_code == 404

    def test_update_config(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """管理员应能更新配置."""
        payload = {
            "description": "更新后的描述",
            "sync_mode": "full",
        }
        response = _patch(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}",
            payload,
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "更新后的描述"
        assert data["sync_mode"] == "full"

    def test_delete_config(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """管理员应能删除配置."""
        config_id = sync_config_for_api.pk
        response = _delete(
            client,
            f"/api/v1/sync/configs/{config_id}",
            _auth(admin_user),
        )
        assert response.status_code == 200
        assert not SyncConfig.objects.filter(pk=config_id).exists()

    def test_delete_config_not_found(self, client: Client, admin_user: User) -> None:
        """删除不存在的配置应返回 404."""
        response = _delete(client, "/api/v1/sync/configs/99999", _auth(admin_user))
        assert response.status_code == 404


class TestSyncTriggerAPI:
    """同步触发 API 测试."""

    def test_trigger_requires_confirm(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """未确认应返回 400."""
        response = _post(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}/trigger",
            {"confirm": False},
            _auth(admin_user),
        )
        assert response.status_code == 400

    def test_trigger_paused_config_fails(
        self, client: Client, admin_user: User, sync_config_for_api: SyncConfig
    ) -> None:
        """暂停的配置不应被触发."""
        sync_config_for_api.status = SyncStatus.PAUSED
        sync_config_for_api.save()
        response = _post(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}/trigger",
            {"confirm": True},
            _auth(admin_user),
        )
        assert response.status_code == 400

    def test_trigger_creates_log(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """触发同步应创建日志记录（即便失败）."""
        _ = _post(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}/trigger",
            {"confirm": True},
            _auth(admin_user),
        )
        logs = SyncLog.objects.filter(config=sync_config_for_api)
        assert logs.count() >= 1


class TestSyncPreviewAPI:
    """同步预览 API 测试."""

    def test_preview_returns_preview_data(
        self, client: Client, admin_user: User, sync_config_for_api: SyncConfig
    ) -> None:
        """预览应返回正确数据."""
        response = _post(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}/preview",
            {"force_full": False},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["config_id"] == sync_config_for_api.pk
        assert "target_fields" in data
        assert "pk_fields" in data

    def test_preview_not_found(self, client: Client, admin_user: User) -> None:
        """预览不存在的配置应返回 404."""
        response = _post(
            client,
            "/api/v1/sync/configs/99999/preview",
            {"force_full": False},
            _auth(admin_user),
        )
        assert response.status_code == 404


class TestSyncBatchAPI:
    """批量同步 API 测试."""

    def test_batch_trigger_requires_confirm(
        self, client: Client, admin_user: User, sync_config_for_api: SyncConfig
    ) -> None:
        """批量触发需确认."""
        response = _post(
            client,
            "/api/v1/sync/batch-trigger",
            {"config_ids": [sync_config_for_api.pk], "confirm": False},
            _auth(admin_user),
        )
        assert response.status_code == 400

    def test_batch_trigger_with_valid_configs(
        self, client: Client, admin_user: User, sync_config_for_api: SyncConfig
    ) -> None:
        """批量触发应返回结果."""
        response = _post(
            client,
            "/api/v1/sync/batch-trigger",
            {
                "config_ids": [sync_config_for_api.pk],
                "force_full": True,
                "stop_on_error": False,
                "confirm": True,
            },
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_batch_trigger_empty_list(self, client: Client, admin_user: User) -> None:
        """空列表应返回零结果."""
        response = _post(
            client,
            "/api/v1/sync/batch-trigger",
            {"config_ids": [], "confirm": True},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestSyncScheduleAPI:
    """调度配置 API 测试."""

    def test_update_schedule(self, client: Client, admin_user: User, sync_config_for_api: SyncConfig) -> None:
        """更新调度设置应成功."""
        response = _post(
            client,
            f"/api/v1/sync/configs/{sync_config_for_api.pk}/schedule",
            {
                "scheduler_enabled": True,
                "cron_expression": "*/5 * * * *",
                "max_retries": 2,
            },
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["scheduler_enabled"] is True
        assert data["cron_expression"] == "*/5 * * * *"
        assert data["max_retries"] == 2

    def test_run_scheduled(self, client: Client, admin_user: User) -> None:
        """执行定时同步应返回结果."""
        response = _post(
            client,
            "/api/v1/sync/scheduled",
            {},
            _auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert "total" in data

    def test_update_schedule_not_found(self, client: Client, admin_user: User) -> None:
        """更新不存在的配置调度应返回 404."""
        response = _post(
            client,
            "/api/v1/sync/configs/99999/schedule",
            {"scheduler_enabled": False},
            _auth(admin_user),
        )
        assert response.status_code == 404


class TestSyncColumnsAPI:
    """源表/目标表列信息 API 测试."""

    def test_source_columns(self, client: Client, admin_user: User) -> None:
        """获取源表列信息."""
        response = client.get(
            "/api/v1/sync/source-columns?table=accounts_user",
            **_auth(admin_user),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["table_name"] == "accounts_user"
        assert len(data["columns"]) > 0

    def test_source_columns_no_table(self, client: Client, admin_user: User) -> None:
        """未指定表名应返回 400."""
        response = client.get(
            "/api/v1/sync/source-columns",
            **_auth(admin_user),
        )
        assert response.status_code == 400

    def test_target_columns(
        self, client: Client, admin_user: User, sync_config_for_api: SyncConfig
    ) -> None:
        """获取目标表列信息（:memory: SQLite 无表，返回 500）."""
        ds_id = sync_config_for_api.target_datasource_id
        response = client.get(
            f"/api/v1/sync/target-columns?datasource_id={ds_id}&table=ext_user",
            **_auth(admin_user),
        )
        # :memory: SQLite 中不存在 ext_user 表，应返回 500
        assert response.status_code == 500

    def test_target_columns_missing_params(self, client: Client, admin_user: User) -> None:
        """缺少参数应返回 400."""
        response = client.get(
            "/api/v1/sync/target-columns",
            **_auth(admin_user),
        )
        assert response.status_code == 400

    def test_target_columns_datasource_not_found(self, client: Client, admin_user: User) -> None:
        """不存在的数据源应返回 404."""
        response = client.get(
            "/api/v1/sync/target-columns?datasource_id=99999&table=t",
            **_auth(admin_user),
        )
        assert response.status_code == 404
