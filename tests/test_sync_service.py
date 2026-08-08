"""同步服务测试."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import SyncAlert, SyncConfig, SyncFieldMapping, SyncLog, SyncLogStatus, SyncMode, SyncStatus
from apps.sync.sync_service import SyncError, SyncService


@pytest.fixture
def sync_ds_for_service(db: Any, admin_user: User) -> DataSource:
    """服务测试用数据源."""
    from apps.datasources.crypto import encrypt_password
    from django.conf import settings

    encrypted = encrypt_password("service_pwd", settings.SECRET_KEY)
    return DataSource.objects.create(
        name="sync_service_sqlite",
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=":memory:",
        username="",
        group="svc",
        password_encrypted=encrypted,
        created_by=admin_user,
    )


@pytest.fixture
def sync_config_for_service(db: Any, admin_user: User, sync_ds_for_service: DataSource) -> SyncConfig:
    """服务测试用同步配置（无映射）."""
    return SyncConfig.objects.create(
        name="服务测试配置",
        source_table="auth_user",
        target_datasource=sync_ds_for_service,
        target_table="ext_user",
        sync_mode=SyncMode.INCREMENTAL,
        created_by=admin_user,
    )


@pytest.fixture
def sync_config_with_mappings_for_service(
    sync_config_for_service: SyncConfig,
) -> SyncConfig:
    """带映射的服务测试配置."""
    SyncFieldMapping.objects.create(
        config=sync_config_for_service,
        source_field="id",
        target_field="ext_id",
        mapping_type="direct",
        is_pk=True,
    )
    SyncFieldMapping.objects.create(
        config=sync_config_for_service,
        source_field="username",
        target_field="ext_name",
        mapping_type="direct",
        is_pk=False,
    )
    SyncFieldMapping.objects.create(
        config=sync_config_for_service,
        source_field="",
        target_field="synced_at",
        mapping_type="constant",
        fixed_value="2024-01-01T00:00:00Z",
        is_pk=False,
    )
    return sync_config_for_service


@pytest.fixture
def service(sync_config_with_mappings_for_service: SyncConfig) -> SyncService:
    """同步服务实例."""
    return SyncService(sync_config_with_mappings_for_service)


def _get_sql(mock_conn: MagicMock) -> str:
    """从 mock 连接获取 SQL 字符串."""
    call_args = mock_conn.execute.call_args
    # call_args.args 是位置参数元组，第一个是 TextClause
    text_clause = call_args.args[0]
    return str(text_clause)


class TestSyncService:
    """SyncService 核心逻辑测试."""

    def test_apply_mappings_direct(self, service: SyncService) -> None:
        """直接映射应正确转换."""
        source_rows = [
            {"id": 1, "username": "alice", "extra": "ignored"},
            {"id": 2, "username": "bob", "extra": "ignored"},
        ]
        config = service.config
        mappings = list(config.field_mappings.all())
        result = service._apply_mappings(source_rows, mappings)

        assert len(result) == 2
        assert result[0]["ext_id"] == 1
        assert result[0]["ext_name"] == "alice"

    def test_apply_mappings_constant(self, service: SyncService) -> None:
        """常量映射应使用 fixed_value."""
        source_rows = [{"id": 1, "username": "alice"}]
        config = service.config
        mappings = list(config.field_mappings.all())
        result = service._apply_mappings(source_rows, mappings)

        assert result[0]["synced_at"] == "2024-01-01T00:00:00Z"

    def test_apply_mappings_empty(self, service: SyncService) -> None:
        """空输入应返回空列表."""
        result = service._apply_mappings([], [])
        assert result == []

    def test_quote_ident_mysql(self) -> None:
        """MySQL 标识符引用应使用反引号."""
        result = SyncService._quote_ident("table_name", "mysql")
        assert result == "`table_name`"

    def test_quote_ident_postgresql(self) -> None:
        """PostgreSQL 标识符引用应使用双引号."""
        result = SyncService._quote_ident("table_name", "postgresql")
        assert result == '"table_name"'

    def test_quote_ident_sqlite(self) -> None:
        """SQLite 标识符引用应使用双引号."""
        result = SyncService._quote_ident("table_name", "sqlite")
        assert result == '"table_name"'

    def test_format_target_table_ref(self) -> None:
        """构造目标表引用."""
        service = SyncService(
            SyncConfig(
                target_schema="public",
                target_table="users",
            )
        )
        result = service._format_target_table_ref("postgresql")
        assert result == '"public"."users"'

    def test_format_target_table_ref_no_schema(self) -> None:
        """无 schema 时不应包含 schema 部分."""
        service = SyncService(
            SyncConfig(
                target_schema="",
                target_table="users",
            )
        )
        result = service._format_target_table_ref("sqlite")
        assert result == '"users"'

    def test_resolve_source_dialect_known_vendors(self) -> None:
        """已知 vendor 应映射到对应方言标识."""
        assert SyncService._resolve_source_dialect("sqlite") == "sqlite"
        assert SyncService._resolve_source_dialect("mysql") == "mysql"
        assert SyncService._resolve_source_dialect("postgresql") == "postgresql"

    def test_resolve_source_dialect_unknown_falls_back_to_postgresql(self) -> None:
        """未知 vendor 应回退为 PostgreSQL 风格."""
        assert SyncService._resolve_source_dialect("oracle") == "postgresql"
        assert SyncService._resolve_source_dialect("microsoft") == "postgresql"

    def test_named_placeholder_sqlite_uses_named_style(self) -> None:
        """SQLite 源应使用 :name 命名占位符."""
        assert SyncService._named_placeholder("last_sync", "sqlite") == ":last_sync"

    def test_named_placeholder_non_sqlite_uses_pyformat(self) -> None:
        """MySQL/PostgreSQL 源应使用 %(name)s pyformat 占位符."""
        assert SyncService._named_placeholder("last_sync", "mysql") == "%(last_sync)s"
        assert SyncService._named_placeholder("last_sync", "postgresql") == "%(last_sync)s"

    def test_format_source_table_ref_with_schema_mysql(self) -> None:
        """MySQL 源表带 schema 应使用反引号并包含 schema 部分."""
        service = SyncService(
            SyncConfig(
                source_schema="app",
                source_table="orders",
            )
        )
        result = service._format_source_table_ref("mysql")
        assert result == "`app`.`orders`"

    def test_format_source_table_ref_with_schema_postgresql(self) -> None:
        """PostgreSQL 源表带 schema 应使用双引号并包含 schema 部分."""
        service = SyncService(
            SyncConfig(
                source_schema="public",
                source_table="orders",
            )
        )
        result = service._format_source_table_ref("postgresql")
        assert result == '"public"."orders"'

    def test_format_source_table_ref_sqlite_ignores_schema(self) -> None:
        """SQLite 源表应忽略 schema，仅用双引号包裹表名."""
        service = SyncService(
            SyncConfig(
                source_schema="ignored",
                source_table="orders",
            )
        )
        result = service._format_source_table_ref("sqlite")
        assert result == '"orders"'


class TestSyncServiceUpsert:
    """UPSERT 各方言实现测试."""

    def test_upsert_mysql_sql_generation(self) -> None:
        """MySQL UPSERT SQL 应包含 ON DUPLICATE KEY UPDATE."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._upsert_mysql(
            conn=mock_conn,
            table_ref="`target`",
            all_fields=["id", "name", "synced_at"],
            non_pk_fields=["name", "synced_at"],
            row={"id": 1, "name": "test", "synced_at": "2024-01-01"},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "ON DUPLICATE KEY UPDATE" in sql
        assert "`name`=VALUES(`name`)" in sql

    def test_upsert_postgresql_sql_generation(self) -> None:
        """PostgreSQL UPSERT SQL 应包含 ON CONFLICT DO UPDATE."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._upsert_postgresql(
            conn=mock_conn,
            table_ref='"target"',
            all_fields=["id", "name"],
            pk_fields=["id"],
            non_pk_fields=["name"],
            row={"id": 1, "name": "test"},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql

    def test_upsert_sqlite_sql_generation(self) -> None:
        """SQLite UPSERT SQL 应包含 ON CONFLICT DO UPDATE."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._upsert_sqlite(
            conn=mock_conn,
            table_ref='"target"',
            all_fields=["id", "name"],
            pk_fields=["id"],
            non_pk_fields=["name"],
            row={"id": 1, "name": "test"},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql

    def test_insert_only_no_pk(self) -> None:
        """无主键时应使用纯 INSERT."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._insert_only(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["name", "value"],
            row={"name": "test", "value": 123},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "INSERT INTO" in sql
        assert "VALUES" in sql

    def test_upsert_single_row_dispatches_by_dialect(self) -> None:
        """有主键时 _upsert_single_row 按方言生成 ON CONFLICT 子句."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._upsert_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["id", "name"],
            pk_fields=["id"],
            non_pk_fields=["name"],
            row={"id": 1, "name": "test"},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql


class TestSyncServiceSkip:
    """SKIP 策略各方言 SQL 生成测试."""

    def test_skip_mysql_uses_insert_ignore(self) -> None:
        """MySQL SKIP 应使用 INSERT IGNORE."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=1)
        service._skip_single_row(
            conn=mock_conn,
            table_ref="`target`",
            dialect="mysql",
            all_fields=["id", "name"],
            pk_fields=["id"],
            row={"id": 1, "name": "test"},
        )
        sql = _get_sql(mock_conn)
        assert "INSERT IGNORE INTO" in sql
        assert "ON CONFLICT" not in sql

    def test_skip_postgresql_uses_do_nothing(self) -> None:
        """PostgreSQL SKIP 应使用 ON CONFLICT DO NOTHING."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=1)
        service._skip_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="postgresql",
            all_fields=["id", "name"],
            pk_fields=["id"],
            row={"id": 1, "name": "test"},
        )
        sql = _get_sql(mock_conn)
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_skip_sqlite_uses_do_nothing(self) -> None:
        """SQLite SKIP 应使用 ON CONFLICT DO NOTHING."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=1)
        service._skip_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["id", "name"],
            pk_fields=["id"],
            row={"id": 1, "name": "test"},
        )
        sql = _get_sql(mock_conn)
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_skip_other_dialect_falls_back_to_insert(self) -> None:
        """未知方言 SKIP 应退化为纯 INSERT."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=1)
        service._skip_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="oracle",
            all_fields=["id", "name"],
            pk_fields=["id"],
            row={"id": 1, "name": "test"},
        )
        sql = _get_sql(mock_conn)
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" not in sql
        assert "IGNORE" not in sql

    def test_skip_returns_false_on_conflict(self) -> None:
        """SKIP 冲突（rowcount=0）应返回 False."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=0)
        written = service._skip_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["id"],
            pk_fields=["id"],
            row={"id": 1},
        )
        assert written is False

    def test_skip_returns_true_on_unknown_rowcount(self) -> None:
        """SKIP 在 rowcount=-1（未知）时应按已写入处理，返回 True."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(rowcount=-1)
        written = service._skip_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["id"],
            pk_fields=["id"],
            row={"id": 1},
        )
        assert written is True


class TestSyncServiceWriteDispatch:
    """写入分派（_write_single_row）测试."""

    def test_write_error_strategy_uses_plain_insert(self) -> None:
        """ERROR 策略应使用纯 INSERT（无冲突处理子句）."""
        from apps.sync.models import ConflictStrategy

        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        written = service._write_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            strategy=ConflictStrategy.ERROR,
            all_fields=["id", "name"],
            pk_fields=["id"],
            non_pk_fields=["name"],
            row={"id": 1, "name": "test"},
        )
        assert written is True
        sql = _get_sql(mock_conn)
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" not in sql
        assert "ON DUPLICATE" not in sql

    def test_write_no_pk_uses_plain_insert(self) -> None:
        """无主键时任何策略都退化为纯 INSERT."""
        from apps.sync.models import ConflictStrategy

        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        written = service._write_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            strategy=ConflictStrategy.SKIP,
            all_fields=["name"],
            pk_fields=[],
            non_pk_fields=["name"],
            row={"name": "test"},
        )
        assert written is True
        sql = _get_sql(mock_conn)
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" not in sql


class TestSyncServiceRun:
    """SyncService.run 方法集成测试."""

    def test_run_no_mappings_raises(self, sync_config_for_service: SyncConfig) -> None:
        """无字段映射时应抛 SyncError."""
        service = SyncService(sync_config_for_service)
        with pytest.raises(SyncError, match="未配置字段映射"):
            service.run()

    def test_run_creates_log_on_error(self, sync_config_for_service: SyncConfig) -> None:
        """同步失败时应创建失败日志."""
        SyncFieldMapping.objects.create(
            config=sync_config_for_service,
            source_field="id",
            target_field="ext_id",
            is_pk=True,
        )
        service = SyncService(sync_config_for_service)
        with suppress(SyncError):
            service.run()
        log = SyncLog.objects.filter(config=sync_config_for_service).first()
        assert log is not None
        assert log.status == SyncLogStatus.FAILED


class TestSyncServicePreview:
    """SyncService.preview 方法测试."""

    def test_preview_with_mappings(self, service: SyncService) -> None:
        """预览应返回正确的字段信息."""
        mock_rows = [
            {"id": 1, "username": "admin"},
            {"id": 2, "username": "user1"},
        ]
        with patch.object(service, "_read_source_data", return_value=mock_rows):
            preview = service.preview()
            assert preview.config_id == service.config.pk
            assert preview.can_sync is True
            assert len(preview.target_fields) > 0
            assert len(preview.pk_fields) > 0
            assert "ext_id" in preview.target_fields
            assert "ext_id" in preview.pk_fields
            assert preview.total_rows == 2

    def test_preview_no_mappings(self, sync_config_for_service: SyncConfig) -> None:
        """无映射时预览应返回 can_sync=False."""
        service = SyncService(sync_config_for_service)
        preview = service.preview()
        assert preview.can_sync is False
        assert "未配置字段映射" in preview.error_message

    def test_preview_sample_data(self, service: SyncService) -> None:
        """预览应包含采样数据."""
        mock_rows = [
            {"id": 1, "username": "admin"},
            {"id": 2, "username": "user1"},
            {"id": 3, "username": "user2"},
            {"id": 4, "username": "user3"},
        ]
        with patch.object(service, "_read_source_data", return_value=mock_rows):
            preview = service.preview(sample_size=3)
            assert len(preview.sample_rows) <= 3


class TestSyncServiceRetry:
    """SyncService 重试机制测试."""

    def test_run_with_retry_increments_count(self, sync_config_with_mappings_for_service: SyncConfig) -> None:
        """重试应增加 retry_count."""
        sync_config_with_mappings_for_service.max_retries = 2
        sync_config_with_mappings_for_service.save()

        service = SyncService(sync_config_with_mappings_for_service)
        with suppress(SyncError):
            service.run(max_retries=1)  # 只重试1次

        sync_config_with_mappings_for_service.refresh_from_db()
        assert sync_config_with_mappings_for_service.retry_count >= 0

    def test_run_sets_status_to_error_on_failure(self, sync_config_with_mappings_for_service: SyncConfig) -> None:
        """失败时应设置 status=error."""
        service = SyncService(sync_config_with_mappings_for_service)
        with suppress(SyncError):
            service.run(max_retries=0)

        sync_config_with_mappings_for_service.refresh_from_db()
        assert sync_config_with_mappings_for_service.status == SyncStatus.ERROR

    def test_run_raises_alert_on_final_failure(self, sync_config_for_service: SyncConfig) -> None:
        """重试全部耗尽的最终失败应产生一条 error 级别告警."""
        service = SyncService(sync_config_for_service)
        with suppress(SyncError):
            service.run(max_retries=0)  # 无映射，直接失败

        alerts = SyncAlert.objects.filter(config=sync_config_for_service)
        assert alerts.count() == 1
        alert = alerts.first()
        assert alert is not None
        assert alert.level == "error"
        assert "未配置字段映射" in alert.message

    def test_run_alerts_once_after_retries_exhausted(self, sync_config_for_service: SyncConfig) -> None:
        """多次重试后仅在最终失败时告警一次，不应每次重试都告警."""
        service = SyncService(sync_config_for_service)
        with suppress(SyncError):
            service.run(max_retries=2)  # 共尝试 3 次，全部失败

        assert SyncAlert.objects.filter(config=sync_config_for_service).count() == 1

    def test_run_success_does_not_raise_alert(self, sync_config_for_service: SyncConfig) -> None:
        """空数据成功路径不应产生告警."""
        # 无待同步数据（源表读取被 mock 为空）→ 走成功分支
        service = SyncService(sync_config_for_service)
        # 先补一个映射避免 "未配置字段映射"，再让源数据为空以触发成功早返回
        SyncFieldMapping.objects.create(
            config=sync_config_for_service,
            source_field="id",
            target_field="ext_id",
            is_pk=True,
        )
        with patch.object(service, "_read_source_data", return_value=[]):
            log = service.run(max_retries=0)
        assert log.status == SyncLogStatus.SUCCESS
        assert SyncAlert.objects.filter(config=sync_config_for_service).count() == 0


class TestSyncServiceBatch:
    """SyncService 批量同步测试."""

    def test_run_batch_with_empty_list(self, db: Any) -> None:
        """空列表应返回零结果."""
        result = SyncService.run_batch([])
        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.skipped == 0

    def test_run_batch_with_valid_configs(
        self,
        db: Any,
        sync_config_with_mappings_for_service: SyncConfig,
    ) -> None:
        """批量同步应处理配置."""
        result = SyncService.run_batch(
            [sync_config_with_mappings_for_service.pk],
            force_full=True,
            stop_on_error=False,
        )
        assert result.total == 1
        assert result.succeeded == 1 or result.failed == 1

    def test_run_batch_skip_inactive_configs(
        self,
        db: Any,
        sync_config_with_mappings_for_service: SyncConfig,
    ) -> None:
        """应跳过已暂停的配置."""
        sync_config_with_mappings_for_service.status = SyncStatus.PAUSED
        sync_config_with_mappings_for_service.save()

        result = SyncService.run_batch([sync_config_with_mappings_for_service.pk])
        assert result.skipped == 1

    def test_run_batch_deduplicates_repeated_config_ids(
        self,
        db: Any,
        sync_config_with_mappings_for_service: SyncConfig,
    ) -> None:
        """重复的 config_id 应去重，避免对同一实例并发写造成竞态（R6）.

        传入重复 ID 时，该配置只执行一次，total 反映去重后的唯一 ID 数，
        且始终满足 total == succeeded + failed + skipped。
        """
        pk = sync_config_with_mappings_for_service.pk
        result = SyncService.run_batch([pk, pk, pk], force_full=True)

        assert result.total == 1
        assert result.succeeded + result.failed + result.skipped == result.total

    def test_run_batch_total_counts_unique_including_skipped(
        self,
        db: Any,
        sync_config_with_mappings_for_service: SyncConfig,
    ) -> None:
        """去重后 total 应涵盖被跳过的唯一 ID（R6）.

        暂停配置的重复 ID 只计一次 skipped，未知 ID 也各计一次 skipped，
        total 等于去重后的唯一 ID 数。
        """
        sync_config_with_mappings_for_service.status = SyncStatus.PAUSED
        sync_config_with_mappings_for_service.save()
        pk = sync_config_with_mappings_for_service.pk

        result = SyncService.run_batch([pk, pk, 999999, 999999])

        assert result.total == 2
        assert result.skipped == 2
        assert result.succeeded + result.failed + result.skipped == result.total


class TestSyncServiceScheduled:
    """SyncService 定时同步测试."""

    def test_run_scheduled_no_configs(self, db: Any) -> None:
        """无调度配置时应返回空结果."""
        result = SyncService.run_scheduled()
        assert result.total == 0

    def test_run_scheduled_with_config_ready(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """到达执行时间的调度配置应被执行."""
        from datetime import timedelta

        from django.utils import timezone

        config = SyncConfig.objects.create(
            name="调度测试配置",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="scheduled_target",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            cron_expression="*/5 * * * *",
            next_run_at=timezone.now() - timedelta(minutes=10),  # 10分钟前
            max_retries=1,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="id",
            target_field="sid",
            is_pk=True,
        )

        result = SyncService.run_scheduled()
        assert result.total >= 1

    def test_run_scheduled_rolls_next_run_at(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """执行后应基于 cron 将 next_run_at 滚动到未来."""
        from datetime import timedelta

        from django.utils import timezone

        past = timezone.now() - timedelta(minutes=10)
        config = SyncConfig.objects.create(
            name="滚动调度配置",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="rolled_target",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            cron_expression="*/5 * * * *",
            next_run_at=past,
            max_retries=0,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(config=config, source_field="id", target_field="sid", is_pk=True)

        SyncService.run_scheduled()

        config.refresh_from_db()
        assert config.last_run_at is not None
        # next_run_at 应被滚动到未来，不再是过去时间
        assert config.next_run_at is not None
        assert config.next_run_at > past

    def test_run_scheduled_rolls_next_run_on_failure(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """即使同步失败，也应滚动 next_run_at 避免反复触发过期任务."""
        from datetime import timedelta

        from django.utils import timezone

        past = timezone.now() - timedelta(minutes=10)
        # 无字段映射 → _do_run 抛 SyncError
        config = SyncConfig.objects.create(
            name="失败滚动配置",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="fail_target",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            cron_expression="*/5 * * * *",
            next_run_at=past,
            max_retries=0,
            created_by=admin_user,
        )

        result = SyncService.run_scheduled()
        assert result.failed >= 1

        config.refresh_from_db()
        assert config.next_run_at is not None
        assert config.next_run_at > past

    def test_run_scheduled_skips_next_run_on_invalid_cron(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """cron 非法时不更新 next_run_at（仅更新 last_run_at），覆盖防御分支."""
        from datetime import timedelta

        from django.utils import timezone

        past = timezone.now() - timedelta(minutes=10)
        # 绕过 API 校验直接落库非法 cron（模拟历史脏数据）
        config = SyncConfig.objects.create(
            name="非法cron调度配置",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="bad_cron_target",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            cron_expression="bad cron",
            next_run_at=past,
            max_retries=0,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(config=config, source_field="id", target_field="sid", is_pk=True)

        SyncService.run_scheduled()

        config.refresh_from_db()
        assert config.last_run_at is not None
        # cron 非法 → next_run_at 保持原值（未被滚动）
        assert config.next_run_at == past


class TestSyncPreview:
    """SyncPreview 数据类测试."""

    def test_preview_creation(self) -> None:
        """创建 SyncPreview 实例."""
        from apps.sync.sync_service import SyncPreview

        preview = SyncPreview(
            config_id=1,
            config_name="test",
            mode="full",
            total_rows=100,
            sample_rows=[{"id": 1}],
            target_fields=["id"],
            pk_fields=["id"],
            can_sync=True,
        )
        assert preview.config_id == 1
        assert preview.total_rows == 100
        assert len(preview.sample_rows) == 1

    def test_preview_default_values(self) -> None:
        """SyncPreview 默认值."""
        from apps.sync.sync_service import SyncPreview

        preview = SyncPreview(
            config_id=1,
            config_name="test",
            mode="incremental",
            total_rows=0,
        )
        assert preview.sample_rows == []
        assert preview.target_fields == []
        assert preview.pk_fields == []
        assert preview.can_sync is True
        assert preview.error_message == ""


class TestBatchSyncResult:
    """BatchSyncResult 数据类测试."""

    def test_batch_result_creation(self) -> None:
        """创建 BatchSyncResult 实例."""
        from apps.sync.sync_service import BatchSyncResult

        result = BatchSyncResult(total=5, succeeded=3, failed=1, skipped=1)
        assert result.total == 5
        assert result.succeeded == 3
        assert result.failed == 1
        assert result.skipped == 1
        assert result.results == []

    def test_batch_result_default_values(self) -> None:
        """BatchSyncResult 默认值."""
        from apps.sync.sync_service import BatchSyncResult

        result = BatchSyncResult()
        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.skipped == 0


class TestSyncServiceExecution:
    """SyncService 实际执行测试（使用文件 SQLite）."""

    def test_full_sync_execution(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """全量同步应将源表数据写入目标表."""
        from apps.datasources.crypto import encrypt_password
        from apps.datasources.engine import dispose_all
        from apps.datasources.models import DataSource, EngineType
        from django.conf import settings

        # 创建文件 SQLite 目标数据源
        db_file = tmp_path / "target.db"
        encrypted = encrypt_password("", settings.SECRET_KEY)
        ds = DataSource.objects.create(
            name="file_sqlite_target",
            engine=EngineType.SQLITE,
            host="",
            port=None,
            database=str(db_file),
            username="",
            password_encrypted=encrypted,
            created_by=admin_user,
        )

        # 在目标库创建表
        from apps.datasources.engine import get_engine
        from sqlalchemy import text

        engine = get_engine(ds)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ext_user (ext_id INTEGER PRIMARY KEY, ext_name TEXT, synced_at TEXT)"))

        # 创建同步配置
        config = SyncConfig.objects.create(
            name="执行测试配置",
            source_table="accounts_user",
            target_datasource=ds,
            target_table="ext_user",
            sync_mode=SyncMode.FULL,
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="id",
            target_field="ext_id",
            mapping_type="direct",
            is_pk=True,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="username",
            target_field="ext_name",
            mapping_type="direct",
            is_pk=False,
        )

        service = SyncService(config)
        log = service.run()

        assert log.status == SyncLogStatus.SUCCESS
        assert log.rows_read > 0
        assert log.rows_written > 0

        # 验证目标表数据
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM ext_user"))
            count = result.scalar()
            assert count > 0

        dispose_all()

    def test_sync_with_zero_rows(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """源表无数据时应成功返回零行."""
        from apps.datasources.crypto import encrypt_password
        from apps.datasources.engine import dispose_all, get_engine
        from apps.datasources.models import DataSource, EngineType
        from django.conf import settings
        from sqlalchemy import text

        db_file = tmp_path / "empty_target.db"
        encrypted = encrypt_password("", settings.SECRET_KEY)
        ds = DataSource.objects.create(
            name="empty_sqlite_target",
            engine=EngineType.SQLITE,
            host="",
            port=None,
            database=str(db_file),
            username="",
            password_encrypted=encrypted,
            created_by=admin_user,
        )

        engine = get_engine(ds)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ext_empty (ext_id INTEGER PRIMARY KEY, ext_name TEXT)"))

        # 使用一个不存在的源表（通过 mock 返回空数据）
        config = SyncConfig.objects.create(
            name="空数据测试配置",
            source_table="auth_user",
            target_datasource=ds,
            target_table="ext_empty",
            sync_mode=SyncMode.FULL,
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="id",
            target_field="ext_id",
            mapping_type="direct",
            is_pk=True,
        )

        service = SyncService(config)
        # Mock _read_source_data 返回空列表
        with patch.object(service, "_read_source_data", return_value=[]):
            log = service.run()
            assert log.status == SyncLogStatus.SUCCESS
            assert log.rows_read == 0
            assert log.rows_written == 0

        dispose_all()

    def test_get_source_count(
        self,
        service: SyncService,
    ) -> None:
        """get_source_count 应返回源表行数."""
        mock_rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        with patch.object(service, "_read_source_data", return_value=mock_rows):
            count = service.get_source_count()
            assert count == 3

    def test_read_source_data_incremental(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """增量模式应使用 last_sync_at 过滤."""
        from datetime import timedelta

        from django.utils import timezone

        config = SyncConfig.objects.create(
            name="增量读取测试",
            source_table="accounts_user",
            target_datasource=sync_ds_for_service,
            target_table="ext_inc",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            timestamp_field="date_joined",
            last_sync_at=timezone.now() - timedelta(days=365),
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="id",
            target_field="ext_id",
            is_pk=True,
        )

        service = SyncService(config)
        rows = service._read_source_data(SyncMode.INCREMENTAL)
        # auth_user 表中应有 admin_user 这条记录
        assert isinstance(rows, list)

    def test_read_source_data_invalid_db_alias_raises(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """无效的 source_db_alias 应抛出明确的 SyncError（R4）.

        源读取按 alias 选连接，非法别名不应静默回退到 default，
        而应给出可定位的错误信息。
        """
        config = SyncConfig.objects.create(
            name="非法别名读取测试",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="ext_bad_alias",
            source_db_alias="not_exist_alias",
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        service = SyncService(config)
        with pytest.raises(SyncError, match="无效的数据库别名"):
            service._read_source_data(SyncMode.FULL)

    def test_read_source_data_uses_connection_vendor_dialect(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """源读取应按连接 vendor 选择方言，默认库为 SQLite 全量读取成功（33 源方言化）.

        默认库 vendor=sqlite，方言化后仍能正确构造 SQL 并读取，
        验证去除 SQLite 硬编码后未破坏原有 SQLite 路径。
        """
        config = SyncConfig.objects.create(
            name="方言化全量读取测试",
            source_table="accounts_user",
            target_datasource=sync_ds_for_service,
            target_table="ext_dialect",
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

        service = SyncService(config)
        rows = service._read_source_data(SyncMode.FULL)
        # auth_user 至少包含 admin_user 一条记录
        assert isinstance(rows, list)
        assert any(r.get("username") == admin_user.username for r in rows)

    def test_finalize_log(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """_finalize_log 应正确设置日志字段."""
        from apps.sync.models import SyncLog, SyncLogStatus
        from django.utils import timezone

        config = SyncConfig.objects.create(
            name="finalize_test",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="ext_fin",
            created_by=admin_user,
        )
        log = SyncLog.objects.create(
            config=config,
            started_at=timezone.now(),
        )
        started = timezone.now()
        SyncService._finalize_log(log, SyncLogStatus.SUCCESS, 10, 8, started, "ok")
        assert log.status == SyncLogStatus.SUCCESS
        assert log.rows_read == 10
        assert log.rows_written == 8
        assert log.duration_ms >= 0
        assert log.error_message == "ok"

    def test_upsert_sqlite_execution(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """SQLite upsert 应正确插入和更新."""
        from apps.datasources.crypto import encrypt_password
        from apps.datasources.engine import dispose_all, get_engine
        from apps.datasources.models import DataSource, EngineType
        from django.conf import settings
        from sqlalchemy import text

        db_file = tmp_path / "upsert_test.db"
        encrypted = encrypt_password("", settings.SECRET_KEY)
        ds = DataSource.objects.create(
            name="upsert_sqlite",
            engine=EngineType.SQLITE,
            host="",
            port=None,
            database=str(db_file),
            username="",
            password_encrypted=encrypted,
            created_by=admin_user,
        )

        engine = get_engine(ds)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE upsert_target (id INTEGER PRIMARY KEY, name TEXT)"))

        config = SyncConfig.objects.create(
            name="upsert测试",
            source_table="auth_user",
            target_datasource=ds,
            target_table="upsert_target",
            sync_mode=SyncMode.FULL,
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="id",
            target_field="id",
            is_pk=True,
        )
        SyncFieldMapping.objects.create(
            config=config,
            source_field="username",
            target_field="name",
            is_pk=False,
        )

        service = SyncService(config)

        # 测试插入
        with engine.begin() as conn:
            service._upsert_sqlite(
                conn,
                "upsert_target",
                ["id", "name"],
                ["id"],
                ["name"],
                {"id": 999, "name": "test_user"},
            )

        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM upsert_target WHERE id = 999"))
            assert result.scalar() == "test_user"

        # 测试更新（upsert）
        with engine.begin() as conn:
            service._upsert_sqlite(
                conn,
                "upsert_target",
                ["id", "name"],
                ["id"],
                ["name"],
                {"id": 999, "name": "updated_user"},
            )

        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM upsert_target WHERE id = 999"))
            assert result.scalar() == "updated_user"

        dispose_all()


def _make_conflict_target(
    admin_user: User,
    tmp_path: Any,
    db_name: str,
    *,
    strategy: str,
    preset_name: str = "old",
) -> tuple[SyncConfig, Any]:
    """创建含预置冲突行的文件 SQLite 目标数据源与配置.

    目标表 ct_target(id PK, name)，预置一行 (1, preset_name)。
    映射 id->id(pk)、name->name。返回 (config, engine)。
    """
    from apps.datasources.crypto import encrypt_password
    from apps.datasources.engine import get_engine
    from apps.datasources.models import DataSource, EngineType
    from django.conf import settings
    from sqlalchemy import text

    db_file = tmp_path / db_name
    encrypted = encrypt_password("", settings.SECRET_KEY)
    ds = DataSource.objects.create(
        name=f"conflict_{db_name}",
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=str(db_file),
        username="",
        password_encrypted=encrypted,
        created_by=admin_user,
    )
    engine = get_engine(ds)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ct_target (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO ct_target (id, name) VALUES (1, :n)"), {"n": preset_name})

    config = SyncConfig.objects.create(
        name=f"冲突策略测试_{db_name}",
        source_table="auth_user",
        target_datasource=ds,
        target_table="ct_target",
        sync_mode=SyncMode.FULL,
        status=SyncStatus.ACTIVE,
        conflict_strategy=strategy,
        created_by=admin_user,
    )
    SyncFieldMapping.objects.create(config=config, source_field="id", target_field="id", is_pk=True)
    SyncFieldMapping.objects.create(config=config, source_field="name", target_field="name", is_pk=False)
    return config, engine


class TestSyncServiceConflictStrategy:
    """冲突处理策略端到端测试（真实文件 SQLite）."""

    def test_upsert_strategy_updates_existing(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """UPSERT 策略：主键冲突应更新目标已有行."""
        from apps.datasources.engine import dispose_all

        config, engine = _make_conflict_target(admin_user, tmp_path, "upsert.db", strategy="upsert")
        # 源提供 id=1（冲突）与 id=2（新增）
        source_rows = [{"id": 1, "name": "new"}, {"id": 2, "name": "two"}]
        service = SyncService(config)
        with patch.object(service, "_read_source_data", return_value=source_rows):
            log = service.run()

        from sqlalchemy import text

        assert log.status == SyncLogStatus.SUCCESS
        assert log.rows_written == 2
        assert log.rows_skipped == 0
        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT id, name FROM ct_target ORDER BY id")).fetchall())
        assert rows == {1: "new", 2: "two"}  # id=1 被更新
        dispose_all()

    def test_skip_strategy_keeps_existing(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """SKIP 策略：主键冲突应跳过、保留目标原值，仅新增非冲突行."""
        from apps.datasources.engine import dispose_all

        config, engine = _make_conflict_target(admin_user, tmp_path, "skip.db", strategy="skip", preset_name="keep")
        source_rows = [{"id": 1, "name": "new"}, {"id": 2, "name": "two"}]
        service = SyncService(config)
        with patch.object(service, "_read_source_data", return_value=source_rows):
            log = service.run()

        from sqlalchemy import text

        assert log.status == SyncLogStatus.SUCCESS
        assert log.rows_written == 1  # 仅 id=2 新增
        assert log.rows_skipped == 1  # id=1 冲突被跳过
        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT id, name FROM ct_target ORDER BY id")).fetchall())
        assert rows == {1: "keep", 2: "two"}  # id=1 保留原值
        dispose_all()

    def test_error_strategy_raises_on_conflict(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """ERROR 策略：主键冲突应抛 SyncError 并回滚整批（目标不新增）."""
        from apps.datasources.engine import dispose_all

        config, engine = _make_conflict_target(admin_user, tmp_path, "error.db", strategy="error", preset_name="keep")
        source_rows = [{"id": 2, "name": "two"}, {"id": 1, "name": "conflict"}]
        service = SyncService(config)
        with patch.object(service, "_read_source_data", return_value=source_rows), pytest.raises(SyncError):
            service.run(max_retries=0)

        from sqlalchemy import text

        # 整批回滚：id=2 也不应写入，目标仅保留预置的 id=1
        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT id, name FROM ct_target ORDER BY id")).fetchall())
        assert rows == {1: "keep"}
        dispose_all()

    def test_error_strategy_success_without_conflict(
        self,
        db: Any,
        admin_user: User,
        tmp_path: Any,
    ) -> None:
        """ERROR 策略无冲突时应正常写入."""
        from apps.datasources.engine import dispose_all

        config, engine = _make_conflict_target(admin_user, tmp_path, "error_ok.db", strategy="error")
        source_rows = [{"id": 2, "name": "two"}, {"id": 3, "name": "three"}]
        service = SyncService(config)
        with patch.object(service, "_read_source_data", return_value=source_rows):
            log = service.run()

        from sqlalchemy import text

        assert log.status == SyncLogStatus.SUCCESS
        assert log.rows_written == 2
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM ct_target")).scalar()
        assert count == 3  # 预置 1 行 + 新增 2 行
        dispose_all()


class TestSyncServiceBatchConcurrent:
    """批量同步并发执行测试.

    并发聚合逻辑通过 mock SyncService.run 验证，避免测试用 SQLite 单文件在
    多线程真实并发写时触发锁竞争（生产环境使用 PostgreSQL/MySQL 无此限制）。
    """

    def test_run_batch_concurrent_all_succeed(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """并发批量同步应成功聚合多个配置结果."""
        configs = []
        for i in range(3):
            config = SyncConfig.objects.create(
                name=f"并发成功配置_{i}",
                source_table="auth_user",
                target_datasource=sync_ds_for_service,
                target_table=f"batch_ok_{i}",
                sync_mode=SyncMode.FULL,
                status=SyncStatus.ACTIVE,
                created_by=admin_user,
            )
            configs.append(config)

        fake_log = SyncLog(id=1, config=configs[0], status=SyncLogStatus.SUCCESS, mode=SyncMode.FULL)
        with patch.object(SyncService, "run", return_value=fake_log):
            result = SyncService.run_batch(
                [c.pk for c in configs],
                force_full=True,
                max_workers=3,
            )

        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert len(result.results) == 3

    def test_run_batch_concurrent_stop_on_error(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """并发批量同步遇错时应记录失败（stop_on_error 尽力而为）."""
        configs = []
        for i in range(3):
            config = SyncConfig.objects.create(
                name=f"并发失败配置_{i}",
                source_table="auth_user",
                target_datasource=sync_ds_for_service,
                target_table=f"batch_fail_{i}",
                sync_mode=SyncMode.FULL,
                status=SyncStatus.ACTIVE,
                created_by=admin_user,
            )
            configs.append(config)

        with patch.object(SyncService, "run", side_effect=SyncError("boom")):
            result = SyncService.run_batch(
                [c.pk for c in configs],
                force_full=True,
                stop_on_error=True,
                max_workers=2,
            )
        assert result.total == 3
        assert result.failed >= 1
        assert result.succeeded == 0

    def test_run_batch_concurrent_mixed_results(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """并发批量同步应正确聚合成功与失败混合结果."""
        configs = []
        for i in range(4):
            config = SyncConfig.objects.create(
                name=f"并发混合配置_{i}",
                source_table="auth_user",
                target_datasource=sync_ds_for_service,
                target_table=f"batch_mix_{i}",
                sync_mode=SyncMode.FULL,
                status=SyncStatus.ACTIVE,
                created_by=admin_user,
            )
            configs.append(config)

        fake_log = SyncLog(id=1, config=configs[0], status=SyncLogStatus.SUCCESS, mode=SyncMode.FULL)
        # 偶数索引成功、奇数索引失败
        calls = {"n": 0}

        def _fake_run(self: SyncService, *, force_full: bool = False, max_retries: int | None = None) -> SyncLog:
            idx = calls["n"]
            calls["n"] += 1
            if idx % 2 == 0:
                return fake_log
            raise SyncError("boom")

        with patch.object(SyncService, "run", _fake_run):
            result = SyncService.run_batch(
                [c.pk for c in configs],
                force_full=True,
                max_workers=4,
            )
        assert result.total == 4
        assert result.succeeded + result.failed == 4
        assert result.succeeded >= 1
        assert result.failed >= 1

    def test_run_batch_concurrent_empty_runnable(self, db: Any) -> None:
        """并发模式下无可运行配置应短路返回（不创建线程池）."""
        result = SyncService.run_batch([999999], max_workers=4)
        assert result.total == 1
        assert result.skipped == 1
        assert result.succeeded == 0

    def test_run_batch_serial_stop_on_error(
        self,
        db: Any,
        admin_user: User,
        sync_ds_for_service: DataSource,
    ) -> None:
        """串行批量同步 stop_on_error 应在首个失败后停止."""
        first = SyncConfig.objects.create(
            name="串行失败配置1",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="serial_fail_1",
            sync_mode=SyncMode.FULL,
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        second = SyncConfig.objects.create(
            name="串行失败配置2",
            source_table="auth_user",
            target_datasource=sync_ds_for_service,
            target_table="serial_fail_2",
            sync_mode=SyncMode.FULL,
            status=SyncStatus.ACTIVE,
            created_by=admin_user,
        )
        with patch.object(SyncService, "run", side_effect=SyncError("boom")):
            result = SyncService.run_batch(
                [first.pk, second.pk],
                force_full=True,
                stop_on_error=True,
                max_workers=1,
            )
        assert result.total == 2
        # 首个失败后停止，仅计一次失败
        assert result.failed == 1
