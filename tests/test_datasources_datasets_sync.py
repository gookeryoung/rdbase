"""数据集同步触发 API（POST /{slug}/sync）端到端测试.

覆盖：
- 成功触发：返回 202 + task_id，写 SYNC_TRIGGER 审计。
- 数据集未绑定 sync_config 返回 400。
- 数据集不存在返回 404；is_active=False 返回 404。
- 同步配置 is_active=False 返回 400。
- 锁占用返回 409。
- 幂等命中返回缓存 task_id。
- scope 不足返回 403；无 Token 返回 401。
- 后台线程启动 SyncService.run（mock 验证调用）。
"""

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from apps.accounts.models import ApiToken, User
from apps.audit.models import AuditAction, AuditLog
from apps.datasources.models import Dataset, DataSource, EngineType
from apps.sync.models import SyncConfig, SyncFieldMapping, SyncMode, SyncStatus
from django.http import HttpResponse
from django.test import Client


def _make_token(user: User, scopes: list[str] | None = None) -> tuple[str, ApiToken]:
    """生成 ApiToken（默认 sync:trigger scope）."""
    return ApiToken.generate(
        name="sync-token",
        created_by=user,
        scopes=scopes if scopes is not None else ["sync:trigger"],
    )


def _token_client(plaintext: str) -> Client:
    """构造携带 X-API-Token 头的客户端."""
    return Client(HTTP_X_API_TOKEN=plaintext)


def _post(client: Client, url: str) -> HttpResponse:
    return cast(HttpResponse, client.post(url))


def _post_idem(client: Client, url: str, idem_key: str) -> HttpResponse:
    return cast(HttpResponse, client.post(url, HTTP_IDEMPOTENCY_KEY=idem_key))


@pytest.fixture
def sync_ds(db: Any, admin_user: User) -> DataSource:
    """SQLite 数据源."""
    return DataSource.objects.create(
        name="ds-dataset-sync",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def sync_config(db: Any, admin_user: User, sync_ds: DataSource) -> SyncConfig:
    """同步配置."""
    config = SyncConfig.objects.create(
        name="cfg-dataset-sync",
        source_table="auth_user",
        target_datasource=sync_ds,
        target_table="ext",
        sync_mode=SyncMode.FULL,
        status=SyncStatus.ACTIVE,
        created_by=admin_user,
    )
    SyncFieldMapping.objects.create(
        config=config,
        source_field="id",
        target_field="ext_id",
        is_pk=True,
    )
    return config


@pytest.fixture
def dataset_with_sync(db: Any, admin_user: User, sync_ds: DataSource, sync_config: SyncConfig) -> Dataset:
    """绑定了 sync_config 的数据集."""
    return Dataset.objects.create(
        slug="sync-target",
        name="同步目标",
        datasource=sync_ds,
        table_name="ext",
        sync_config=sync_config,
        is_active=True,
    )


class TestDatasetSyncTrigger:
    """触发端点测试."""

    def test_trigger_success_returns_task_id(
        self, client: Client, admin_user: User, dataset_with_sync: Dataset
    ) -> None:
        """成功触发应返回 202 + task_id + sync_config_id."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        with patch("apps.datasources.datasets_api.SyncService") as mock_svc:
            mock_svc.return_value.run.return_value = MagicMock()
            resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")

        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data
        assert len(data["task_id"]) == 32  # uuid4().hex
        assert data["sync_config_id"] == dataset_with_sync.sync_config_id
        assert data["status"] == "accepted"

    def test_trigger_writes_audit(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """成功触发应写 SYNC_TRIGGER 审计."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        with patch("apps.datasources.datasets_api.SyncService"):
            _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")

        audit = AuditLog.objects.filter(
            action=AuditAction.SYNC_TRIGGER,
            resource_type="dataset",
            resource_id=str(dataset_with_sync.pk),
        )
        assert audit.exists()

    def test_trigger_no_sync_config(self, client: Client, admin_user: User, sync_ds: DataSource) -> None:
        """未绑定 sync_config 应返回 400."""
        ds = Dataset.objects.create(
            slug="no-sync-cfg",
            name="无同步",
            datasource=sync_ds,
            table_name="ext",
        )
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/datasets/{ds.slug}/sync")
        assert resp.status_code == 400

    def test_trigger_dataset_not_found(self, client: Client, admin_user: User) -> None:
        """数据集不存在应返回 404."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, "/api/v1/datasets/nonexistent/sync")
        assert resp.status_code == 404

    def test_trigger_inactive_dataset_404(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """is_active=False 数据集应返回 404."""
        dataset_with_sync.is_active = False
        dataset_with_sync.save()
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
        assert resp.status_code == 404

    def test_trigger_paused_sync_config_400(
        self, client: Client, admin_user: User, dataset_with_sync: Dataset, sync_config: SyncConfig
    ) -> None:
        """同步配置 is_active=False 应返回 400."""
        sync_config.status = SyncStatus.PAUSED
        sync_config.save()
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
        assert resp.status_code == 400

    def test_trigger_lock_contention_409(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """锁被占用应返回 409."""
        from apps.system.distributed_lock import DistributedLock

        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        holder = DistributedLock(f"sync:config:{dataset_with_sync.sync_config_id}")
        assert holder.acquire() is True
        try:
            resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
            assert resp.status_code == 409
        finally:
            holder.release()

    def test_trigger_idempotency_cached(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """相同 Idempotency-Key 应返回相同 task_id."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        with patch("apps.datasources.datasets_api.SyncService") as mock_svc:
            mock_svc.return_value.run.return_value = MagicMock()
            resp1 = _post_idem(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync", "idem-1")
            resp2 = _post_idem(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync", "idem-1")

        assert resp1.status_code == 202
        assert resp2.status_code == 202
        assert resp1.json()["task_id"] == resp2.json()["task_id"]
        # SyncService.run 仅被调用一次
        assert mock_svc.return_value.run.call_count == 1

    def test_trigger_scope_insufficient(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """无 sync:trigger scope 应返回 403."""
        plaintext, _ = _make_token(admin_user, scopes=["datasets:read"])
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
        assert resp.status_code == 403

    def test_trigger_no_token_401(self, client: Client, admin_user: User, dataset_with_sync: Dataset) -> None:
        """无 Token 应返回 401."""
        c = Client()
        resp = _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
        assert resp.status_code == 401

    def test_trigger_starts_background_thread(
        self, client: Client, admin_user: User, dataset_with_sync: Dataset
    ) -> None:
        """触发应启动后台线程执行 SyncService.run."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        with patch("apps.datasources.datasets_api.SyncService") as mock_svc:
            mock_svc.return_value.run.return_value = MagicMock()
            _post(c, f"/api/v1/datasets/{dataset_with_sync.slug}/sync")
            # 等待后台线程执行（最多 1s）
            for _ in range(20):
                if mock_svc.return_value.run.called:
                    break
                time.sleep(0.05)
            assert mock_svc.return_value.run.called
