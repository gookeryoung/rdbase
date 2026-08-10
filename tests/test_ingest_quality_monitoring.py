"""P8-Q3 质量监控告警测试.

覆盖：
- ``_coerce_threshold`` 阈值解析（合法/非法/越界）
- ``ValidationPipeline._compute_quality_score`` 质量分计算
- ``ValidationPipeline._maybe_raise_quality_alert`` 阈值告警（WARNING/CRITICAL/无告警）
- ``ValidationPipeline.open_spider`` 读取 ``quality_thresholds``
- ``ValidationPipeline.close_spider`` 写入 ``IngestLog.quality_score`` 与产生告警
- ``IngestLog.quality_score`` 默认值
- ``IngestLog.aggregate_stats`` 含 ``avg_quality_score``
- ``IngestQualityReport.field_health`` 类方法（全局/按任务/recent 限制/排序）
- API：``GET /ingest/field-health`` / ``GET /ingest/tasks/{id}/field-health``
- API：``GET /ingest/stats`` 返回 ``avg_quality_score``
- API：``GET /ingest/tasks/{id}/logs`` 返回 ``quality_score``
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import (
    AlertLevel,
    IngestAlert,
    IngestLog,
    IngestLogStatus,
    IngestQualityReport,
    IngestTask,
    SourceType,
)
from apps.ingest.validation import (
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_WARNING_THRESHOLD,
    ValidationPipeline,
    _coerce_threshold,
)
from django.test import Client
from django.utils import timezone

# ----------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------


class _FakeStats:
    """假 stats 收集器."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get_value(self, key: str, default: Any = 0) -> Any:
        return self.values.get(key, default)

    def inc_value(self, key: str, count: int = 1) -> None:
        self.values[key] = self.values.get(key, 0) + count


class _FakeSpider:
    """假 spider，携带 validation_config 与 task_id."""

    def __init__(
        self,
        *,
        validation_config: dict[str, Any] | None = None,
        task_id: int | None = 42,
    ) -> None:
        self.validation_config = validation_config or {}
        self.task_id = task_id


# ----------------------------------------------------------------
# _coerce_threshold
# ----------------------------------------------------------------


class TestCoerceThreshold:
    def test_int_value(self) -> None:
        assert _coerce_threshold(80, 80.0) == 80.0

    def test_float_value(self) -> None:
        assert _coerce_threshold(75.5, 80.0) == 75.5

    def test_string_numeric(self) -> None:
        assert _coerce_threshold("85", 80.0) == 85.0

    def test_string_invalid_falls_back(self) -> None:
        assert _coerce_threshold("abc", 80.0) == 80.0

    def test_none_falls_back(self) -> None:
        assert _coerce_threshold(None, 80.0) == 80.0

    def test_negative_clamped_to_zero(self) -> None:
        assert _coerce_threshold(-5, 80.0) == 0.0

    def test_over_100_clamped_to_100(self) -> None:
        assert _coerce_threshold(150, 80.0) == 100.0

    def test_tuple_invalid_falls_back(self) -> None:
        # 非数字类型应回退到默认值
        assert _coerce_threshold((1, 2), 80.0) == 80.0

    def test_list_invalid_falls_back(self) -> None:
        assert _coerce_threshold([1, 2], 60.0) == 60.0


# ----------------------------------------------------------------
# _compute_quality_score
# ----------------------------------------------------------------


class TestComputeQualityScore:
    def test_no_checks_returns_100(self) -> None:
        pipeline = ValidationPipeline()
        assert pipeline._compute_quality_score() == 100.0

    def test_all_passed_returns_100(self) -> None:
        pipeline = ValidationPipeline()
        pipeline._total_checks = 10
        pipeline._passed_checks = 10
        assert pipeline._compute_quality_score() == 100.0

    def test_half_passed_returns_50(self) -> None:
        pipeline = ValidationPipeline()
        pipeline._total_checks = 10
        pipeline._passed_checks = 5
        assert pipeline._compute_quality_score() == 50.0

    def test_rounded_to_one_decimal(self) -> None:
        pipeline = ValidationPipeline()
        pipeline._total_checks = 3
        pipeline._passed_checks = 2
        # 2/3 * 100 = 66.666... → 66.7
        assert pipeline._compute_quality_score() == 66.7


# ----------------------------------------------------------------
# _maybe_raise_quality_alert
# ----------------------------------------------------------------


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    return DataSource.objects.create(
        name="ds_qmon",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def task_with_log(db: Any, datasource: DataSource) -> tuple[IngestTask, IngestLog]:
    task = IngestTask.objects.create(
        name="t-qmon",
        source_type=SourceType.API,
        source_url="https://example.com",
        target_datasource=datasource,
        target_table="out",
    )
    log = IngestLog.objects.create(
        task=task,
        status=IngestLogStatus.SUCCESS,
        rows_read=10,
        rows_written=10,
        rows_skipped=0,
        started_at=timezone.now() - timedelta(seconds=5),
        finished_at=timezone.now(),
        duration_ms=5000,
    )
    return task, log


class TestMaybeRaiseQualityAlert:
    def test_no_alert_when_score_above_warning(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, _ = task_with_log
        pipeline = ValidationPipeline()
        pipeline._maybe_raise_quality_alert(task, 95.0)
        assert IngestAlert.objects.filter(task=task).count() == 0

    def test_warning_alert_when_score_below_warning(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, _ = task_with_log
        pipeline = ValidationPipeline()
        pipeline._maybe_raise_quality_alert(task, 75.0)
        alerts = list(IngestAlert.objects.filter(task=task))
        assert len(alerts) == 1
        assert alerts[0].level == AlertLevel.WARNING
        assert "75.0" in alerts[0].message
        assert "WARNING" in alerts[0].message

    def test_critical_alert_when_score_below_critical(
        self, db: Any, task_with_log: tuple[IngestTask, IngestLog]
    ) -> None:
        task, _ = task_with_log
        pipeline = ValidationPipeline()
        pipeline._maybe_raise_quality_alert(task, 40.0)
        alerts = list(IngestAlert.objects.filter(task=task))
        assert len(alerts) == 1
        assert alerts[0].level == AlertLevel.ERROR
        assert "40.0" in alerts[0].message
        assert "CRITICAL" in alerts[0].message

    def test_custom_thresholds_from_config(
        self,
        db: Any,
        task_with_log: tuple[IngestTask, IngestLog],
    ) -> None:
        """从 validation_config.quality_thresholds 读取自定义阈值."""
        task, _ = task_with_log
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                "quality_thresholds": {"warning": 90, "critical": 70},
            },
        )
        pipeline.open_spider(spider)
        assert pipeline._warning_threshold == 90.0
        assert pipeline._critical_threshold == 70.0
        # 85 < 90 (warning) 但 >= 70 (critical) → WARNING
        pipeline._maybe_raise_quality_alert(task, 85.0)
        alerts = list(IngestAlert.objects.filter(task=task))
        assert len(alerts) == 1
        assert alerts[0].level == AlertLevel.WARNING

    def test_custom_thresholds_invalid_falls_back(self) -> None:
        """非法阈值配置回退到默认."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                "quality_thresholds": {"warning": "abc", "critical": None},
            },
        )
        pipeline.open_spider(spider)
        assert pipeline._warning_threshold == DEFAULT_WARNING_THRESHOLD
        assert pipeline._critical_threshold == DEFAULT_CRITICAL_THRESHOLD

    def test_custom_thresholds_non_dict_ignored(self) -> None:
        """quality_thresholds 非字典时忽略，保持默认."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                "quality_thresholds": [80, 60],
            },
        )
        pipeline.open_spider(spider)
        assert pipeline._warning_threshold == DEFAULT_WARNING_THRESHOLD
        assert pipeline._critical_threshold == DEFAULT_CRITICAL_THRESHOLD

    def test_thresholds_clamped_to_range(self) -> None:
        """阈值超出 0-100 截断到边界."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                "quality_thresholds": {"warning": -5, "critical": 150},
            },
        )
        pipeline.open_spider(spider)
        assert pipeline._warning_threshold == 0.0
        assert pipeline._critical_threshold == 100.0


# ----------------------------------------------------------------
# close_spider 写入 quality_score 与告警
# ----------------------------------------------------------------


class TestCloseSpiderQualityScore:
    def test_close_spider_writes_quality_score_to_log(
        self,
        db: Any,
        admin_user: Any,
    ) -> None:
        ds = DataSource.objects.create(
            name="ds_score",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-score",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=4,
            rows_written=4,
            rows_skipped=0,
            started_at=timezone.now() - timedelta(seconds=10),
            finished_at=timezone.now(),
            duration_ms=10000,
        )

        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
            },
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 4 条：2 通过 + 2 失败 → 质量分 50.0
        pipeline.process_item({"name": "a"}, spider)
        pipeline.process_item({"name": "b"}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.close_spider(spider)

        log.refresh_from_db()
        assert log.quality_score == 50.0
        # stats 也写入 quality_score
        assert pipeline._stats.values["ingest_quality_score"] == 50.0

    def test_close_spider_no_rules_writes_default_100(
        self,
        db: Any,
        admin_user: Any,
    ) -> None:
        """无校验规则时 quality_score 写入 100（视为全部通过）."""
        ds = DataSource.objects.create(
            name="ds_no_rules",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-no-rules",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={},  # 空配置
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.close_spider(spider)

        log = IngestLog.objects.filter(task=task).order_by("-started_at").first()
        assert log is not None
        assert log.quality_score == 100.0
        # 无规则时不产生告警
        assert IngestAlert.objects.filter(task=task).count() == 0

    def test_close_spider_raises_warning_alert(
        self,
        db: Any,
        admin_user: Any,
    ) -> None:
        """quality_score 低于 warning 阈值时产生 WARNING 告警."""
        ds = DataSource.objects.create(
            name="ds_warn",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-warn",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=10,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                # 提高阈值触发告警
                "quality_thresholds": {"warning": 90, "critical": 50},
            },
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 4 条：3 通过 + 1 失败 → 75.0 < 90 (warning) 但 >= 50 (critical)
        pipeline.process_item({"name": "a"}, spider)
        pipeline.process_item({"name": "b"}, spider)
        pipeline.process_item({"name": "c"}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.close_spider(spider)

        alerts = list(IngestAlert.objects.filter(task=task))
        assert len(alerts) == 1
        assert alerts[0].level == AlertLevel.WARNING

    def test_close_spider_raises_critical_alert(
        self,
        db: Any,
        admin_user: Any,
    ) -> None:
        """quality_score 低于 critical 阈值时产生 ERROR 告警."""
        ds = DataSource.objects.create(
            name="ds_crit",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-crit",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=10,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [{"field": "name", "op": "required"}],
                "quality_thresholds": {"warning": 80, "critical": 60},
            },
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 4 条：1 通过 + 3 失败 → 25.0 < 60 (critical)
        pipeline.process_item({"name": "a"}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.close_spider(spider)

        alerts = list(IngestAlert.objects.filter(task=task))
        assert len(alerts) == 1
        assert alerts[0].level == AlertLevel.ERROR

    def test_close_spider_no_alert_when_score_above_thresholds(
        self,
        db: Any,
        admin_user: Any,
    ) -> None:
        """quality_score 高于 warning 阈值时不产生告警."""
        ds = DataSource.objects.create(
            name="ds_ok",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-ok",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=10,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "name", "op": "required"}]},
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 4 条全部通过 → 100.0
        for v in ["a", "b", "c", "d"]:
            pipeline.process_item({"name": v}, spider)
        pipeline.close_spider(spider)

        assert IngestAlert.objects.filter(task=task).count() == 0

    def test_close_spider_no_task_id_skips_alert(self) -> None:
        """无 task_id 时仅写 stats，不写 quality_score 与告警."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "x", "op": "required"}]},
            task_id=None,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"x": "a"}, spider)
        # 不应抛异常
        pipeline.close_spider(spider)
        # stats 仍写入
        assert pipeline._stats.values["ingest_quality_score"] == 100.0


# ----------------------------------------------------------------
# IngestLog.quality_score 默认值与 aggregate_stats
# ----------------------------------------------------------------


class TestIngestLogQualityScore:
    def test_quality_score_default_100(self, db: Any, datasource: DataSource) -> None:
        task = IngestTask.objects.create(
            name="t-default-score",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=0,
            rows_written=0,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=0,
        )
        assert log.quality_score == 100.0

    def test_aggregate_stats_avg_quality_score_no_logs(self, db: Any) -> None:
        stats = IngestLog.aggregate_stats()
        assert stats.avg_quality_score == 0.0

    def test_aggregate_stats_avg_quality_score(self, db: Any, datasource: DataSource) -> None:
        task = IngestTask.objects.create(
            name="t-agg-score",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        for score in (80.0, 90.0, 100.0):
            IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=10,
                rows_written=10,
                rows_skipped=0,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                duration_ms=100,
                quality_score=score,
            )
        stats = IngestLog.aggregate_stats()
        # 均值 (80 + 90 + 100) / 3 = 90.0
        assert stats.avg_quality_score == 90.0

    def test_aggregate_stats_avg_quality_score_with_task_filter(
        self,
        db: Any,
        datasource: DataSource,
    ) -> None:
        task1 = IngestTask.objects.create(
            name="t-filter1",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        task2 = IngestTask.objects.create(
            name="t-filter2",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task1,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
            quality_score=70.0,
        )
        IngestLog.objects.create(
            task=task2,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
            quality_score=100.0,
        )
        stats = IngestLog.aggregate_stats(task_id=task1.pk)
        assert stats.avg_quality_score == 70.0


# ----------------------------------------------------------------
# IngestQualityReport.field_health
# ----------------------------------------------------------------


class TestFieldHealth:
    def test_field_health_empty(self, db: Any) -> None:
        result = IngestQualityReport.field_health()
        assert result == []

    def test_field_health_single_task(
        self,
        db: Any,
        task_with_log: tuple[IngestTask, IngestLog],
    ) -> None:
        task, log = task_with_log
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="required",
            total_count=10,
            passed_count=8,
            failed_count=2,
            pass_rate=80.0,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="age",
            rule="range",
            total_count=10,
            passed_count=5,
            failed_count=5,
            pass_rate=50.0,
        )
        result = IngestQualityReport.field_health()
        assert len(result) == 2
        # 按平均通过率升序（最差在前）：age(50) < name(80)
        assert result[0]["field"] == "age"
        assert result[0]["avg_pass_rate"] == 50.0
        assert result[0]["total_checks"] == 10
        assert result[0]["total_failures"] == 5
        assert result[0]["last_pass_rate"] == 50.0
        assert result[0]["samples"] == 1
        assert result[1]["field"] == "name"
        assert result[1]["avg_pass_rate"] == 80.0

    def test_field_health_filter_by_task(
        self,
        db: Any,
        datasource: DataSource,
    ) -> None:
        task1 = IngestTask.objects.create(
            name="t-fh1",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        task2 = IngestTask.objects.create(
            name="t-fh2",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log1 = IngestLog.objects.create(
            task=task1,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        log2 = IngestLog.objects.create(
            task=task2,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        IngestQualityReport.objects.create(
            task=task1,
            log=log1,
            field="name",
            rule="required",
            total_count=10,
            passed_count=10,
            failed_count=0,
            pass_rate=100.0,
        )
        IngestQualityReport.objects.create(
            task=task2,
            log=log2,
            field="age",
            rule="range",
            total_count=10,
            passed_count=5,
            failed_count=5,
            pass_rate=50.0,
        )

        # 限定 task1
        result = IngestQualityReport.field_health(task_id=task1.pk)
        assert len(result) == 1
        assert result[0]["field"] == "name"
        # 全局
        all_result = IngestQualityReport.field_health()
        assert len(all_result) == 2

    def test_field_health_recent_limit(
        self,
        db: Any,
        task_with_log: tuple[IngestTask, IngestLog],
    ) -> None:
        """recent 限制每条 (field, rule) 取最近 N 条报告参与统计."""
        task, log = task_with_log
        # 同一 (field, rule) 创建 5 条报告，通过率递减
        for rate in [100.0, 90.0, 80.0, 70.0, 60.0]:
            IngestQualityReport.objects.create(
                task=task,
                log=log,
                field="name",
                rule="required",
                total_count=10,
                passed_count=int(rate / 10),
                failed_count=10 - int(rate / 10),
                pass_rate=rate,
            )
        # recent=3：取最近 3 条（60, 70, 80），均值 = 70.0
        result = IngestQualityReport.field_health(recent=3)
        assert len(result) == 1
        assert result[0]["samples"] == 3
        assert result[0]["avg_pass_rate"] == 70.0
        # 最近一次为 60.0（created_at 倒序第一条）
        assert result[0]["last_pass_rate"] == 60.0

    def test_field_health_groups_by_field_rule(
        self,
        db: Any,
        task_with_log: tuple[IngestTask, IngestLog],
    ) -> None:
        """同字段不同规则分别聚合."""
        task, log = task_with_log
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="required",
            total_count=10,
            passed_count=8,
            failed_count=2,
            pass_rate=80.0,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="regex",
            total_count=10,
            passed_count=5,
            failed_count=5,
            pass_rate=50.0,
        )
        result = IngestQualityReport.field_health()
        assert len(result) == 2
        # 两组：(name, regex) 50 < (name, required) 80
        groups = {(r["field"], r["rule"]) for r in result}
        assert groups == {("name", "required"), ("name", "regex")}


# ----------------------------------------------------------------
# API 测试
# ----------------------------------------------------------------


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class TestFieldHealthAPI:
    def test_list_field_health_empty(self, db: Any, client: Client, admin_user: Any) -> None:
        resp = client.get("/api/v1/ingest/field-health", **_auth(admin_user))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_field_health_with_data(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-fh-api",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=10,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="required",
            total_count=10,
            passed_count=8,
            failed_count=2,
            pass_rate=80.0,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="age",
            rule="range",
            total_count=10,
            passed_count=5,
            failed_count=5,
            pass_rate=50.0,
        )

        resp = client.get("/api/v1/ingest/field-health", **_auth(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # 升序：age(50) 在前
        assert body[0]["field"] == "age"
        assert body[0]["avg_pass_rate"] == 50.0
        assert body[0]["total_checks"] == 10
        assert body[0]["total_failures"] == 5
        assert body[0]["samples"] == 1

    def test_list_field_health_filter_by_task_id(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task1 = IngestTask.objects.create(
            name="t-fh-filter1",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        task2 = IngestTask.objects.create(
            name="t-fh-filter2",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log1 = IngestLog.objects.create(
            task=task1,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        log2 = IngestLog.objects.create(
            task=task2,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        IngestQualityReport.objects.create(
            task=task1,
            log=log1,
            field="name",
            rule="required",
            total_count=5,
            passed_count=5,
            failed_count=0,
            pass_rate=100.0,
        )
        IngestQualityReport.objects.create(
            task=task2,
            log=log2,
            field="age",
            rule="range",
            total_count=5,
            passed_count=3,
            failed_count=2,
            pass_rate=60.0,
        )

        resp = client.get(
            f"/api/v1/ingest/field-health?task_id={task1.pk}",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["field"] == "name"

    def test_list_field_health_invalid_task_id_ignored(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
    ) -> None:
        """非数字 task_id 视为 None，返回全局结果."""
        resp = client.get(
            "/api/v1/ingest/field-health?task_id=abc",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_field_health_recent_param(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-fh-recent",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=10,
            rows_written=10,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        for rate in [100.0, 90.0, 80.0]:
            IngestQualityReport.objects.create(
                task=task,
                log=log,
                field="name",
                rule="required",
                total_count=10,
                passed_count=int(rate / 10),
                failed_count=10 - int(rate / 10),
                pass_rate=rate,
            )

        resp = client.get(
            "/api/v1/ingest/field-health?recent=2",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["samples"] == 2

    def test_list_field_health_recent_clamped_to_max(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
    ) -> None:
        """recent 超过 100 截断到 100."""
        resp = client.get(
            "/api/v1/ingest/field-health?recent=500",
            **_auth(admin_user),
        )
        assert resp.status_code == 200

    def test_list_field_health_recent_clamped_to_min(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
    ) -> None:
        """recent 低于 1 截断到 1."""
        resp = client.get(
            "/api/v1/ingest/field-health?recent=0",
            **_auth(admin_user),
        )
        assert resp.status_code == 200

    def test_list_task_field_health(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-task-fh",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="email",
            rule="regex",
            total_count=5,
            passed_count=4,
            failed_count=1,
            pass_rate=80.0,
        )

        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/field-health",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["field"] == "email"
        assert body[0]["rule"] == "regex"

    def test_list_task_field_health_task_not_found(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
    ) -> None:
        resp = client.get(
            "/api/v1/ingest/tasks/99999/field-health",
            **_auth(admin_user),
        )
        assert resp.status_code == 404


class TestStatsAPIWithQuality:
    def test_stats_returns_avg_quality_score(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-stats-q",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
            quality_score=80.0,
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
            quality_score=90.0,
        )

        resp = client.get("/api/v1/ingest/stats", **_auth(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert "avg_quality_score" in body
        # (80 + 90) / 2 = 85.0
        assert body["avg_quality_score"] == 85.0

    def test_stats_no_logs_avg_quality_score_zero(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
    ) -> None:
        resp = client.get("/api/v1/ingest/stats", **_auth(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["avg_quality_score"] == 0.0


class TestLogAPIWithQuality:
    def test_log_out_includes_quality_score(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-log-q",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=100,
            quality_score=75.5,
        )

        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/logs",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["quality_score"] == 75.5
