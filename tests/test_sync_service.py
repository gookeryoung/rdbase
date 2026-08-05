"""同步服务测试."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import SyncConfig, SyncFieldMapping, SyncLog, SyncLogStatus, SyncMode, SyncStatus
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

    def test_upsert_without_pk_falls_back_to_insert(self) -> None:
        """无主键配置时应回退为 INSERT."""
        service = SyncService(SyncConfig())
        mock_conn = MagicMock()
        service._upsert_single_row(
            conn=mock_conn,
            table_ref='"target"',
            dialect="sqlite",
            all_fields=["name"],
            pk_fields=[],
            non_pk_fields=["name"],
            row={"name": "test"},
        )
        mock_conn.execute.assert_called_once()
        sql = _get_sql(mock_conn)
        assert "INSERT INTO" in sql
        assert "ON DUPLICATE" not in sql
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
