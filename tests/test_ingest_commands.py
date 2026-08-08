"""ingest 管理命令测试."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import IngestLog, IngestLogStatus, IngestTask, SourceType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_cmd",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def task(db: Any, datasource: DataSource) -> IngestTask:
    """爬取任务 fixture."""
    return IngestTask.objects.create(
        name="cmd_t",
        source_type=SourceType.API,
        source_url="https://example.com/api",
        target_datasource=datasource,
        target_table="out",
    )


class TestRunIngestCommand:
    """run_ingest 命令测试."""

    def test_success(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        def fake_execute(_task: IngestTask) -> IngestLog:
            return IngestLog.objects.create(
                task=_task,
                status=IngestLogStatus.SUCCESS,
                rows_read=5,
                rows_written=5,
                started_at=timezone.now(),
                duration_ms=10,
            )

        monkeypatch.setattr("apps.ingest.management.commands.run_ingest.execute_task", fake_execute)
        out = StringIO()
        call_command("run_ingest", str(task.pk), stdout=out)
        assert "爬取完成" in out.getvalue()

    def test_failure_raises_command_error(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        def fake_execute(_task: IngestTask) -> IngestLog:
            return IngestLog.objects.create(
                task=_task,
                status=IngestLogStatus.FAILED,
                error_message="boom",
                started_at=timezone.now(),
                duration_ms=5,
            )

        monkeypatch.setattr("apps.ingest.management.commands.run_ingest.execute_task", fake_execute)
        with pytest.raises(CommandError):
            call_command("run_ingest", str(task.pk), stdout=StringIO(), stderr=StringIO())

    def test_not_found(self, db: Any) -> None:
        with pytest.raises(CommandError):
            call_command("run_ingest", "999999", stdout=StringIO(), stderr=StringIO())


class TestRunScheduledIngestCommand:
    """run_scheduled_ingest 命令测试."""

    def test_no_due_tasks(self, db: Any) -> None:
        out = StringIO()
        call_command("run_scheduled_ingest", stdout=out)
        assert "无到期" in out.getvalue()

    def test_runs_due_tasks(self, monkeypatch: pytest.MonkeyPatch, db: Any, datasource: DataSource) -> None:
        # 创建一个到期任务
        task = IngestTask.objects.create(
            name="due_t",
            source_type=SourceType.API,
            source_url="https://x.io",
            target_datasource=datasource,
            target_table="t",
            scheduler_enabled=True,
            cron_expression="*/5 * * * *",
            next_run_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_spawn(_task_id: int) -> Any:
            return FakeResult()

        monkeypatch.setattr("apps.ingest.management.commands.run_scheduled_ingest.spawn_ingest", fake_spawn)
        out = StringIO()
        call_command("run_scheduled_ingest", stdout=out)
        assert "成功 1" in out.getvalue()
        # 滚动 next_run_at
        task.refresh_from_db()
        assert task.next_run_at is not None
        assert task.next_run_at > timezone.now()
