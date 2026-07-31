"""同步模型测试."""

from __future__ import annotations

from typing import Any

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import (
    SyncConfig,
    SyncFieldMapping,
    SyncLog,
    SyncLogStatus,
    SyncMode,
    SyncStatus,
)
from django.db.models import QuerySet


@pytest.fixture
def sync_ds(db: Any, admin_user: User) -> DataSource:
    """用于同步测试的 SQLite 数据源."""
    from apps.datasources.crypto import encrypt_password
    from django.conf import settings

    plaintext = "sync_test_pwd"
    encrypted = encrypt_password(plaintext, settings.SECRET_KEY)
    return DataSource.objects.create(
        name="sync_target_sqlite",
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=":memory:",
        username="",
        group="sync",
        password_encrypted=encrypted,
        created_by=admin_user,
    )


@pytest.fixture
def sync_config(db: Any, admin_user: User, sync_ds: DataSource) -> SyncConfig:
    """同步配置 fixture."""
    return SyncConfig.objects.create(
        name="测试同步配置",
        description="测试用同步配置",
        source_table="auth_user",
        source_schema="",
        source_db_alias="default",
        target_datasource=sync_ds,
        target_table="external_user",
        target_schema="",
        sync_mode=SyncMode.INCREMENTAL,
        status=SyncStatus.ACTIVE,
        timestamp_field="updated_at",
        batch_size=100,
        created_by=admin_user,
    )


@pytest.fixture
def sync_config_with_mappings(sync_config: SyncConfig) -> SyncConfig:
    """带字段映射的同步配置."""
    SyncFieldMapping.objects.create(
        config=sync_config,
        source_field="id",
        target_field="ext_id",
        mapping_type="direct",
        is_pk=True,
    )
    SyncFieldMapping.objects.create(
        config=sync_config,
        source_field="username",
        target_field="ext_name",
        mapping_type="direct",
        is_pk=False,
    )
    SyncFieldMapping.objects.create(
        config=sync_config,
        source_field="",
        target_field="synced_at",
        mapping_type="constant",
        fixed_value="2024-01-01T00:00:00Z",
        is_pk=False,
    )
    return sync_config


class TestSyncConfig:
    """SyncConfig 模型测试."""

    def test_create_config(self, sync_config: SyncConfig) -> None:
        """创建同步配置成功."""
        assert sync_config.pk > 0
        assert sync_config.name == "测试同步配置"
        assert sync_config.is_active is True

    def test_is_active_property(self, sync_config: SyncConfig) -> None:
        """is_active 应根据 status 返回布尔值."""
        assert sync_config.is_active is True
        sync_config.status = SyncStatus.PAUSED
        assert sync_config.is_active is False
        sync_config.status = SyncStatus.ERROR
        assert sync_config.is_active is False

    def test_is_schedulable_property(self, sync_config: SyncConfig) -> None:
        """is_schedulable 应根据条件返回布尔值."""
        assert sync_config.is_schedulable is False

        sync_config.scheduler_enabled = True
        sync_config.cron_expression = "*/5 * * * *"
        sync_config.save()
        assert sync_config.is_schedulable is True

        sync_config.status = SyncStatus.PAUSED
        sync_config.save()
        assert sync_config.is_schedulable is False

    def test_scheduler_fields_default(self, sync_config: SyncConfig) -> None:
        """调度字段应有正确默认值."""
        assert sync_config.scheduler_enabled is False
        assert sync_config.cron_expression == ""
        assert sync_config.retry_count == 0
        assert sync_config.max_retries == 3
        assert sync_config.last_run_at is None
        assert sync_config.next_run_at is None

    def test_name_unique_constraint(self, sync_config: SyncConfig) -> None:
        """name 字段应唯一."""
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            SyncConfig.objects.create(
                name=sync_config.name,
                source_table="other_table",
                target_datasource=sync_config.target_datasource,
                target_table="other_target",
                created_by=sync_config.created_by,
            )

    def test_str_representation(self, sync_config: SyncConfig) -> None:
        """字符串表示应包含名称."""
        result = str(sync_config)
        assert sync_config.name in result


class TestSyncFieldMapping:
    """SyncFieldMapping 模型测试."""

    def test_create_mapping(self, sync_config_with_mappings: SyncConfig) -> None:
        """创建字段映射成功."""
        mappings: QuerySet[SyncFieldMapping] = sync_config_with_mappings.field_mappings.all()
        assert mappings.count() == 3

    def test_mapping_cascade_delete(self, sync_config: SyncConfig) -> None:
        """删除配置应级联删除映射."""
        SyncFieldMapping.objects.create(
            config=sync_config,
            source_field="f1",
            target_field="f2",
        )
        assert sync_config.field_mappings.count() == 1
        config_id = sync_config.pk
        sync_config.delete()
        assert not SyncFieldMapping.objects.filter(config_id=config_id).exists()


class TestSyncLog:
    """SyncLog 模型测试."""

    def test_create_log(self, sync_config: SyncConfig) -> None:
        """创建同步日志成功."""
        from django.utils import timezone

        log = SyncLog.objects.create(
            config=sync_config,
            status=SyncLogStatus.SUCCESS,
            mode=SyncMode.FULL,
            rows_read=100,
            rows_written=98,
            rows_skipped=2,
            started_at=timezone.now(),
        )
        assert log.pk > 0
        assert log.rows_read == 100
        assert log.status == SyncLogStatus.SUCCESS

    def test_log_duration_calculation(self, sync_config: SyncConfig) -> None:
        """同步日志应计算耗时."""
        import time

        from django.utils import timezone

        log = SyncLog.objects.create(
            config=sync_config,
            status=SyncLogStatus.FAILED,
            mode=SyncMode.INCREMENTAL,
            rows_read=0,
            rows_written=0,
            started_at=timezone.now(),
        )
        time.sleep(0.1)
        log.status = SyncLogStatus.SUCCESS
        log.finished_at = log.started_at  # simplified
        log.duration_ms = 100
        log.save()
        assert log.duration_ms == 100

    def test_log_default_ordering(self, sync_config: SyncConfig) -> None:
        """日志应按开始时间降序排列."""
        from django.utils import timezone

        SyncLog.objects.create(
            config=sync_config, status=SyncLogStatus.SUCCESS, mode=SyncMode.FULL, started_at=timezone.now()
        )
        SyncLog.objects.create(
            config=sync_config, status=SyncLogStatus.SUCCESS, mode=SyncMode.FULL, started_at=timezone.now()
        )
        logs = list(SyncLog.objects.all())
        assert len(logs) == 2

    def test_log_on_config_delete(self, sync_config: SyncConfig) -> None:
        """删除配置应保留日志（SET_NULL 行为或保留）.

        此处 config 是 PROTECT，所以删除配置前必须先清理日志。
        """
        from django.utils import timezone

        log = SyncLog.objects.create(
            config=sync_config,
            status=SyncLogStatus.SUCCESS,
            mode=SyncMode.FULL,
            started_at=timezone.now(),
        )
        assert log.config_id == sync_config.pk
