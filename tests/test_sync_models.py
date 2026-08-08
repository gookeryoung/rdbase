"""同步模型测试."""

from __future__ import annotations

from typing import Any

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import (
    AlertLevel,
    SyncAlert,
    SyncConfig,
    SyncFieldMapping,
    SyncLog,
    SyncLogStatus,
    SyncMode,
    SyncStats,
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

    def test_conflict_strategy_default_is_upsert(self, sync_config: SyncConfig) -> None:
        """冲突处理策略默认应为 upsert."""
        from apps.sync.models import ConflictStrategy

        assert sync_config.conflict_strategy == ConflictStrategy.UPSERT

    def test_conflict_strategy_choices(self) -> None:
        """ConflictStrategy 应包含 upsert/skip/error 三种取值."""
        from apps.sync.models import ConflictStrategy

        values = {choice.value for choice in ConflictStrategy}
        assert values == {"upsert", "skip", "error"}

    def test_conflict_strategy_persisted(self, sync_config: SyncConfig) -> None:
        """写入非默认策略后应能从库中读回."""
        from apps.sync.models import ConflictStrategy

        sync_config.conflict_strategy = ConflictStrategy.SKIP
        sync_config.save(update_fields=["conflict_strategy"])
        sync_config.refresh_from_db()
        assert sync_config.conflict_strategy == ConflictStrategy.SKIP

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

    def test_refresh_next_run_when_schedulable(self, sync_config: SyncConfig) -> None:
        """可调度配置应基于 cron 计算并持久化 next_run_at."""
        from django.utils import timezone

        before = timezone.now()
        sync_config.scheduler_enabled = True
        sync_config.cron_expression = "* * * * *"
        sync_config.save()

        result = sync_config.refresh_next_run()
        assert result is not None
        assert result > before
        sync_config.refresh_from_db()
        assert sync_config.next_run_at == result

    def test_refresh_next_run_clears_when_not_schedulable(self, sync_config: SyncConfig) -> None:
        """不可调度时应清空 next_run_at."""
        from django.utils import timezone

        sync_config.next_run_at = timezone.now()
        sync_config.scheduler_enabled = False
        sync_config.save()

        result = sync_config.refresh_next_run()
        assert result is None
        sync_config.refresh_from_db()
        assert sync_config.next_run_at is None

    def test_refresh_next_run_clears_on_invalid_cron(self, sync_config: SyncConfig) -> None:
        """cron 非法时应清空 next_run_at 而非抛异常."""
        sync_config.scheduler_enabled = True
        sync_config.cron_expression = "bad cron"
        sync_config.save()

        result = sync_config.refresh_next_run()
        assert result is None

    def test_refresh_next_run_without_save(self, sync_config: SyncConfig) -> None:
        """save=False 时不应持久化，仅返回计算结果."""
        sync_config.scheduler_enabled = True
        sync_config.cron_expression = "* * * * *"
        sync_config.save()

        result = sync_config.refresh_next_run(save=False)
        assert result is not None
        sync_config.refresh_from_db()
        assert sync_config.next_run_at is None


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


def _make_log(  # noqa: PLR0913
    config: SyncConfig,
    status: str,
    *,
    duration_ms: int = 0,
    rows_read: int = 0,
    rows_written: int = 0,
    rows_skipped: int = 0,
    started_at: Any = None,
) -> SyncLog:
    """构造一条同步日志，便于统计聚合测试复用."""
    from django.utils import timezone

    return SyncLog.objects.create(
        config=config,
        status=status,
        mode=SyncMode.FULL,
        rows_read=rows_read,
        rows_written=rows_written,
        rows_skipped=rows_skipped,
        duration_ms=duration_ms,
        started_at=started_at or timezone.now(),
    )


class TestAggregateStats:
    """SyncLog.aggregate_stats 统计聚合测试."""

    def test_empty_returns_zero_stats(self, db: Any) -> None:
        """无任何日志时各项应为 0，成功率为 0.0."""
        stats = SyncLog.aggregate_stats()
        assert isinstance(stats, SyncStats)
        assert stats.total == 0
        assert stats.succeeded == 0
        assert stats.failed == 0
        assert stats.success_rate == 0.0
        assert stats.avg_duration_ms == 0
        assert stats.total_rows_written == 0

    def test_success_rate_and_avg_duration(self, sync_config: SyncConfig) -> None:
        """成功率仅计完全成功，平均耗时取整."""
        _make_log(sync_config, SyncLogStatus.SUCCESS, duration_ms=100, rows_written=10)
        _make_log(sync_config, SyncLogStatus.SUCCESS, duration_ms=200, rows_written=20)
        _make_log(sync_config, SyncLogStatus.PARTIAL, duration_ms=300, rows_skipped=5)
        _make_log(sync_config, SyncLogStatus.FAILED, duration_ms=400)

        stats = SyncLog.aggregate_stats()
        assert stats.total == 4
        assert stats.succeeded == 2
        assert stats.partial == 1
        assert stats.failed == 1
        # 仅 2/4 完全成功
        assert stats.success_rate == 50.0
        # (100+200+300+400)/4 = 250
        assert stats.avg_duration_ms == 250
        assert stats.total_rows_written == 30
        assert stats.total_rows_skipped == 5

    def test_filter_by_config_id(self, sync_config: SyncConfig, sync_ds: DataSource, admin_user: User) -> None:
        """config_id 过滤应仅统计指定配置的日志."""
        other = SyncConfig.objects.create(
            name="另一个配置",
            source_table="auth_user",
            target_datasource=sync_ds,
            target_table="other_target",
            created_by=admin_user,
        )
        _make_log(sync_config, SyncLogStatus.SUCCESS)
        _make_log(other, SyncLogStatus.FAILED)

        stats = SyncLog.aggregate_stats(config_id=sync_config.pk)
        assert stats.total == 1
        assert stats.succeeded == 1
        assert stats.failed == 0

    def test_filter_by_days(self, sync_config: SyncConfig) -> None:
        """days 过滤应排除早于窗口的日志."""
        from datetime import timedelta

        from django.utils import timezone

        _make_log(sync_config, SyncLogStatus.SUCCESS, started_at=timezone.now())
        _make_log(
            sync_config,
            SyncLogStatus.FAILED,
            started_at=timezone.now() - timedelta(days=10),
        )

        stats = SyncLog.aggregate_stats(days=7)
        assert stats.total == 1
        assert stats.succeeded == 1

    def test_days_non_positive_ignored(self, sync_config: SyncConfig) -> None:
        """days<=0 时应不做时间过滤（视为不限时间）."""
        _make_log(sync_config, SyncLogStatus.SUCCESS)
        stats = SyncLog.aggregate_stats(days=0)
        assert stats.total == 1


class TestSyncAlert:
    """SyncAlert 模型测试."""

    def test_alert_level_choices(self) -> None:
        """AlertLevel 应包含 warning/error 两种取值."""
        values = {choice.value for choice in AlertLevel}
        assert values == {"warning", "error"}

    def test_raise_alert_default_error(self, sync_config: SyncConfig) -> None:
        """raise_alert 默认级别为 error 且写入内容."""
        alert = SyncAlert.raise_alert(sync_config, "同步失败原因")
        assert alert.pk > 0
        assert alert.level == AlertLevel.ERROR
        assert alert.message == "同步失败原因"
        assert alert.acknowledged is False
        assert alert.acknowledged_at is None

    def test_raise_alert_warning_level(self, sync_config: SyncConfig) -> None:
        """可指定 warning 级别."""
        alert = SyncAlert.raise_alert(sync_config, "提示", level=AlertLevel.WARNING)
        assert alert.level == AlertLevel.WARNING

    def test_acknowledge_sets_flag_and_time(self, sync_config: SyncConfig) -> None:
        """确认告警应置 acknowledged=True 并记录确认时间."""
        alert = SyncAlert.raise_alert(sync_config, "失败")
        alert.acknowledge()
        alert.refresh_from_db()
        assert alert.acknowledged is True
        assert alert.acknowledged_at is not None

    def test_acknowledge_without_save(self, sync_config: SyncConfig) -> None:
        """save=False 时仅改内存状态不持久化."""
        alert = SyncAlert.raise_alert(sync_config, "失败")
        alert.acknowledge(save=False)
        assert alert.acknowledged is True
        alert.refresh_from_db()
        assert alert.acknowledged is False

    def test_alert_cascade_delete(self, sync_config: SyncConfig) -> None:
        """删除配置应级联删除其告警."""
        SyncAlert.raise_alert(sync_config, "失败")
        config_id = sync_config.pk
        sync_config.delete()
        assert not SyncAlert.objects.filter(config_id=config_id).exists()

    def test_alert_default_ordering(self, sync_config: SyncConfig) -> None:
        """告警应按创建时间降序排列（最新在前）."""
        first = SyncAlert.raise_alert(sync_config, "第一条")
        second = SyncAlert.raise_alert(sync_config, "第二条")
        alerts = list(SyncAlert.objects.all())
        assert alerts[0].pk == second.pk
        assert alerts[1].pk == first.pk

    def test_str_representation(self, sync_config: SyncConfig) -> None:
        """字符串表示应包含级别与配置名."""
        alert = SyncAlert.raise_alert(sync_config, "失败原因")
        text = str(alert)
        assert sync_config.name in text
        assert AlertLevel.ERROR in text
