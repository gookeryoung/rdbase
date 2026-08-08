"""ingest 引擎编排测试."""

from __future__ import annotations

from typing import Any

import pytest
from apps.datasources.models import DataSource, EngineType
from apps.ingest.engine import (
    IngestError,
    SpiderStats,
    _build_scrapy_settings,
    _resolve_spider,
    execute_task,
    spawn_ingest,
)
from apps.ingest.models import IngestAlert, IngestLogStatus, IngestTask, SourceType
from apps.ingest.spiders.base import BaseIngestSpider


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_engine",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def task(db: Any, datasource: DataSource) -> IngestTask:
    """爬取任务 fixture."""
    return IngestTask.objects.create(
        name="engine_t",
        source_type=SourceType.API,
        source_url="https://example.com/api",
        target_datasource=datasource,
        target_table="out",
        max_retries=2,
    )


class TestSpawnIngest:
    """spawn_ingest 子进程编排测试."""

    def test_uses_subprocess_with_list_args(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        captured: dict[str, Any] = {}

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            captured["shell"] = kwargs.get("shell")
            return FakeResult()

        monkeypatch.setattr("apps.ingest.engine.subprocess.run", fake_run)
        result = spawn_ingest(task.pk)

        assert result.returncode == 0
        # 必须是 list 形参，禁用 shell
        assert captured["shell"] is None
        assert isinstance(captured["cmd"], list)
        assert str(task.pk) in captured["cmd"]
        assert "run_ingest" in captured["cmd"]
        # cwd 指向 backend/（含 manage.py）
        assert "backend" in str(captured["cwd"])

    def test_timeout_passed(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        captured: dict[str, Any] = {}

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            captured["timeout"] = kwargs.get("timeout")
            return FakeResult()

        monkeypatch.setattr("apps.ingest.engine.subprocess.run", fake_run)
        spawn_ingest(task.pk, timeout=30)
        assert captured["timeout"] == 30


class TestExecuteTask:
    """execute_task 日志/告警/重试逻辑测试（mock _run_spider 不真跑 Scrapy）."""

    def test_success_writes_log_and_updates_task(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        def fake_run(_task: IngestTask) -> SpiderStats:
            return SpiderStats(rows_read=10, rows_written=8, rows_skipped=2)

        monkeypatch.setattr("apps.ingest.engine._run_spider", fake_run)

        log = execute_task(task)

        assert log.status == IngestLogStatus.SUCCESS
        assert log.rows_read == 10
        assert log.rows_written == 8
        assert log.rows_skipped == 2
        assert log.finished_at is not None
        assert log.duration_ms >= 0
        task.refresh_from_db()
        assert task.last_sync_at is not None
        assert task.retry_count == 0

    def test_failure_writes_failed_log(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        def fake_run(_task: IngestTask) -> SpiderStats:
            raise IngestError("网络超时")

        monkeypatch.setattr("apps.ingest.engine._run_spider", fake_run)

        log = execute_task(task)

        assert log.status == IngestLogStatus.FAILED
        assert "网络超时" in str(log.error_message)
        task.refresh_from_db()
        assert task.retry_count == 1
        # 未达 max_retries(2)，不产生告警
        assert not IngestAlert.objects.filter(task=task).exists()

    def test_failure_at_max_retries_raises_alert(self, monkeypatch: pytest.MonkeyPatch, task: IngestTask) -> None:
        task.retry_count = 1  # 再失败一次即达 max_retries=2
        task.save(update_fields=["retry_count"])

        def fake_run(_task: IngestTask) -> SpiderStats:
            raise IngestError("致命错误")

        monkeypatch.setattr("apps.ingest.engine._run_spider", fake_run)

        execute_task(task)

        alerts = IngestAlert.objects.filter(task=task)
        assert alerts.count() == 1
        assert "致命错误" in str(alerts.first().message)


class TestResolveSpider:
    """_resolve_spider 分派测试."""

    def test_returns_base_placeholder_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        for st in SourceType:
            spider_cls = _resolve_spider(st.value)
            assert spider_cls is BaseIngestSpider

    def test_invalid_source_type_raises(self) -> None:
        with pytest.raises(IngestError, match="不支持的源类型"):
            _resolve_spider("unknown")


class TestBuildScrapySettings:
    """_build_scrapy_settings 构造测试."""

    def test_defaults(self, task: IngestTask) -> None:
        settings = _build_scrapy_settings(task)
        assert settings["ROBOTSTXT_OBEY"] is True
        assert settings["LOG_ENABLED"] is False
        assert settings["CONCURRENT_REQUESTS"] == 8
        assert settings["DOWNLOAD_TIMEOUT"] == 30

    def test_from_request_config(self, task: IngestTask) -> None:
        task.request_config = {
            "concurrent_requests": 4,
            "timeout": 60,
            "download_delay": 1.5,
            "user_agent": "custom/1.0",
            "cookies_enabled": True,
        }
        settings = _build_scrapy_settings(task)
        assert settings["CONCURRENT_REQUESTS"] == 4
        assert settings["DOWNLOAD_TIMEOUT"] == 60
        assert settings["DOWNLOAD_DELAY"] == 1.5
        assert settings["USER_AGENT"] == "custom/1.0"
        assert settings["COOKIES_ENABLED"] is True

    def test_obey_robots_false(self, task: IngestTask) -> None:
        task.obey_robots = False
        settings = _build_scrapy_settings(task)
        assert settings["ROBOTSTXT_OBEY"] is False
