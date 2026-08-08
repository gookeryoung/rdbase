"""ingest 模型单元测试."""

from __future__ import annotations

from typing import Any

import pytest
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import (
    AlertLevel,
    AuthType,
    ConflictStrategy,
    IngestAlert,
    IngestFieldMapping,
    IngestLog,
    IngestLogStatus,
    IngestStatus,
    IngestTask,
    SourceType,
)
from django.utils import timezone


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_test",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def task(db: Any, datasource: DataSource) -> IngestTask:
    """爬取任务 fixture."""
    return IngestTask.objects.create(
        name="t1",
        source_type=SourceType.API,
        source_url="https://example.com/api",
        target_datasource=datasource,
        target_table="t_out",
    )


class TestEnums:
    """枚举值校验."""

    def test_source_type_choices(self) -> None:
        assert {st.value for st in SourceType} == {"api", "html", "file", "rss"}

    def test_conflict_strategy_matches_sync_semantics(self) -> None:
        assert {c.value for c in ConflictStrategy} == {"upsert", "skip", "error"}

    def test_auth_type_values(self) -> None:
        assert AuthType.NONE.value == "none"
        assert AuthType.BEARER.value == "bearer"


class TestIngestTask:
    """IngestTask 模型测试."""

    def test_defaults(self, task: IngestTask) -> None:
        assert task.is_active is True
        assert task.is_schedulable is False
        assert task.conflict_strategy == ConflictStrategy.UPSERT
        assert task.auth_type == AuthType.NONE
        assert task.obey_robots is True
        assert task.batch_size == 500
        assert task.max_retries == 3

    def test_str(self, task: IngestTask) -> None:
        assert str(task) == "t1"

    def test_is_schedulable_requires_all(self, task: IngestTask) -> None:
        task.scheduler_enabled = True
        task.cron_expression = "*/5 * * * *"
        assert task.is_schedulable is True
        task.status = IngestStatus.PAUSED
        assert task.is_schedulable is False

    def test_set_get_headers_roundtrip(self, task: IngestTask) -> None:
        headers = {"Authorization": "Bearer secret", "X-Api-Key": "abc"}
        task.set_headers(headers)
        # headers_encrypted 不应包含明文
        encrypted = str(task.headers_encrypted)
        assert "secret" not in encrypted
        assert "Bearer secret" not in encrypted
        assert task.get_headers() == headers

    def test_set_empty_headers(self, task: IngestTask) -> None:
        task.set_headers({})
        assert task.headers_encrypted == ""
        assert task.get_headers() == {}

    def test_get_headers_corrupted_returns_empty(self, task: IngestTask) -> None:
        task.headers_encrypted = "not-a-valid-token"
        assert task.get_headers() == {}

    def test_refresh_next_run_schedulable(self, task: IngestTask) -> None:
        task.scheduler_enabled = True
        task.cron_expression = "*/5 * * * *"
        nxt = task.refresh_next_run(save=False)
        assert nxt is not None
        assert nxt > timezone.now()

    def test_refresh_next_run_unschedulable_clears(self, task: IngestTask) -> None:
        task.next_run_at = timezone.now()
        task.refresh_next_run(save=False)
        assert task.next_run_at is None

    def test_refresh_next_run_invalid_cron(self, task: IngestTask) -> None:
        task.scheduler_enabled = True
        task.cron_expression = "not-a-cron"
        # refresh_next_run 内部捕获 CronError 并清空 next_run_at，不向上抛出
        task.refresh_next_run(save=False)
        assert task.next_run_at is None


class TestIngestFieldMapping:
    """字段映射测试."""

    def test_str(self, task: IngestTask) -> None:
        m = IngestFieldMapping.objects.create(task=task, source_field="a", target_field="b")
        assert str(m) == "t1: a → b"


class TestIngestLog:
    """日志与统计测试."""

    def test_aggregate_stats_empty(self, db: Any) -> None:
        stats = IngestLog.aggregate_stats()
        assert stats.total == 0
        assert stats.success_rate == 0.0

    def test_aggregate_stats(self, task: IngestTask) -> None:
        now = timezone.now()
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=8,
            started_at=now,
            duration_ms=100,
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.FAILED,
            rows_read=5,
            rows_written=0,
            started_at=now,
            duration_ms=50,
        )
        stats = IngestLog.aggregate_stats()
        assert stats.total == 2
        assert stats.succeeded == 1
        assert stats.failed == 1
        assert stats.success_rate == 50.0
        assert stats.avg_duration_ms == 75
        assert stats.total_rows_read == 15


class TestIngestAlert:
    """告警测试."""

    def test_raise_alert(self, task: IngestTask) -> None:
        alert = IngestAlert.raise_alert(task, "失败原因", level=AlertLevel.WARNING)
        assert alert.task_id == task.pk
        assert alert.level == AlertLevel.WARNING
        assert alert.acknowledged is False

    def test_acknowledge(self, task: IngestTask) -> None:
        alert = IngestAlert.raise_alert(task, "err")
        alert.acknowledge()
        assert alert.acknowledged is True
        assert alert.acknowledged_at is not None
