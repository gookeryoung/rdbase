"""sync 管理命令测试."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.sync.models import SyncConfig, SyncFieldMapping, SyncMode, SyncStatus
from django.core.management import call_command
from django.utils import timezone


@pytest.fixture
def cmd_ds(db: Any, admin_user: User) -> DataSource:
    """命令测试用数据源."""
    from apps.datasources.crypto import encrypt_password
    from django.conf import settings

    encrypted = encrypt_password("cmd_pwd", settings.SECRET_KEY)
    return DataSource.objects.create(
        name="cmd_target_sqlite",
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=":memory:",
        username="",
        group="cmd",
        password_encrypted=encrypted,
        created_by=admin_user,
    )


class TestRunScheduledSyncCommand:
    """run_scheduled_sync 管理命令测试."""

    def test_no_due_tasks(self, db: Any) -> None:
        """无到期任务时应输出提示."""
        out = StringIO()
        call_command("run_scheduled_sync", stdout=out)
        assert "无到期" in out.getvalue()

    def test_runs_due_task(self, db: Any, admin_user: User, cmd_ds: DataSource) -> None:
        """存在到期任务时应执行并输出摘要."""
        config = SyncConfig.objects.create(
            name="命令调度配置",
            source_table="auth_user",
            target_datasource=cmd_ds,
            target_table="cmd_target",
            sync_mode=SyncMode.INCREMENTAL,
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            cron_expression="*/5 * * * *",
            next_run_at=timezone.now() - timedelta(minutes=10),
            max_retries=0,
            created_by=admin_user,
        )
        SyncFieldMapping.objects.create(config=config, source_field="id", target_field="sid", is_pk=True)

        out = StringIO()
        call_command("run_scheduled_sync", stdout=out)
        assert "定时同步完成" in out.getvalue()
