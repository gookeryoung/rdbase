"""ValidationPipeline 与 IngestQualityReport 测试.

覆盖：
- 6 类校验器（required/range/regex/enum/unique/expression）单独验证
- _RuleStats 累加器
- ValidationPipeline 生命周期（open/process/close + from_crawler）
- 空配置透传、stats 收集、close_spider 写入 IngestQualityReport
- IngestQualityReport 模型与 aggregate_summary
- 质量报告 API（list_task_quality_reports / get_task_quality_summary）
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import (
    IngestLog,
    IngestLogStatus,
    IngestQualityReport,
    IngestTask,
    SourceType,
)
from apps.ingest.validation import (
    MAX_SAMPLES_PER_RULE,
    ValidationPipeline,
    _RuleStats,
    _validate_enum,
    _validate_expression,
    _validate_range,
    _validate_regex,
    _validate_required,
    _validate_unique,
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


class _FakeCrawler:
    def __init__(self) -> None:
        self.stats = _FakeStats()


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
# required 校验器
# ----------------------------------------------------------------


class TestRequired:
    def test_present_value_passes(self) -> None:
        assert _validate_required({"name": "abc"}, {"field": "name"}) is True

    def test_none_fails(self) -> None:
        assert _validate_required({"name": None}, {"field": "name"}) is False

    def test_empty_string_fails(self) -> None:
        assert _validate_required({"name": ""}, {"field": "name"}) is False

    def test_whitespace_fails(self) -> None:
        assert _validate_required({"name": "   "}, {"field": "name"}) is False

    def test_zero_passes(self) -> None:
        # 0 是有效值，不应被当作缺失
        assert _validate_required({"age": 0}, {"field": "age"}) is True

    def test_false_passes(self) -> None:
        assert _validate_required({"flag": False}, {"field": "flag"}) is True

    def test_empty_field_passes(self) -> None:
        # 无 field 不校验，直接通过
        assert _validate_required({"name": "abc"}, {}) is True


# ----------------------------------------------------------------
# range 校验器
# ----------------------------------------------------------------


class TestRange:
    def test_in_range_passes(self) -> None:
        assert _validate_range({"age": 25}, {"field": "age", "min": 0, "max": 150}) is True

    def test_below_min_fails(self) -> None:
        assert _validate_range({"age": -1}, {"field": "age", "min": 0, "max": 150}) is False

    def test_above_max_fails(self) -> None:
        assert _validate_range({"age": 200}, {"field": "age", "min": 0, "max": 150}) is False

    def test_string_numeric_passes(self) -> None:
        assert _validate_range({"age": "25"}, {"field": "age", "min": 0, "max": 150}) is True

    def test_non_numeric_fails(self) -> None:
        assert _validate_range({"age": "abc"}, {"field": "age", "min": 0}) is False

    def test_missing_value_passes(self) -> None:
        # 缺失值由 required 负责，range 不拦截
        assert _validate_range({"age": None}, {"field": "age", "min": 0, "max": 150}) is True

    def test_only_min(self) -> None:
        assert _validate_range({"age": 10}, {"field": "age", "min": 0}) is True
        assert _validate_range({"age": -5}, {"field": "age", "min": 0}) is False

    def test_only_max(self) -> None:
        assert _validate_range({"age": 100}, {"field": "age", "max": 150}) is True
        assert _validate_range({"age": 200}, {"field": "age", "max": 150}) is False

    def test_no_min_max_passes(self) -> None:
        assert _validate_range({"age": 999}, {"field": "age"}) is True

    def test_empty_field_passes(self) -> None:
        assert _validate_range({"age": 25}, {}) is True


# ----------------------------------------------------------------
# regex 校验器
# ----------------------------------------------------------------


class TestRegex:
    def test_match_passes(self) -> None:
        assert (
            _validate_regex(
                {"email": "user@example.com"},
                {"field": "email", "pattern": "^[^@]+@[^@]+$"},
            )
            is True
        )

    def test_no_match_fails(self) -> None:
        assert (
            _validate_regex(
                {"email": "not-an-email"},
                {"field": "email", "pattern": "^[^@]+@[^@]+$"},
            )
            is False
        )

    def test_missing_value_passes(self) -> None:
        assert (
            _validate_regex(
                {"email": None},
                {"field": "email", "pattern": "^[^@]+@[^@]+$"},
            )
            is True
        )

    def test_non_string_value_converted(self) -> None:
        # 数字会被转为字符串再匹配
        assert _validate_regex({"id": 12345}, {"field": "id", "pattern": r"^\d+$"}) is True

    def test_invalid_pattern_fails(self) -> None:
        # 非法正则视为失败（容错）
        assert (
            _validate_regex(
                {"v": "abc"},
                {"field": "v", "pattern": "["},
            )
            is False
        )

    def test_empty_pattern_passes(self) -> None:
        # 空 pattern 视为不校验
        assert _validate_regex({"v": "abc"}, {"field": "v", "pattern": ""}) is True

    def test_non_string_pattern_passes(self) -> None:
        assert _validate_regex({"v": "abc"}, {"field": "v", "pattern": 123}) is True

    def test_empty_field_passes(self) -> None:
        assert _validate_regex({"v": "abc"}, {"field": "v"}) is True

    def test_no_field_passes(self) -> None:
        # 无 field 参数直接通过
        assert _validate_regex({"v": "abc"}, {"pattern": ".*"}) is True


# ----------------------------------------------------------------
# enum 校验器
# ----------------------------------------------------------------


class TestEnum:
    def test_in_list_passes(self) -> None:
        assert (
            _validate_enum(
                {"status": "active"},
                {"field": "status", "values": ["active", "inactive"]},
            )
            is True
        )

    def test_not_in_list_fails(self) -> None:
        assert (
            _validate_enum(
                {"status": "unknown"},
                {"field": "status", "values": ["active", "inactive"]},
            )
            is False
        )

    def test_string_value_matches_int_in_list(self) -> None:
        # str(value) 与 str(v) 比较
        assert (
            _validate_enum(
                {"status": "1"},
                {"field": "status", "values": [1, 2, 3]},
            )
            is True
        )

    def test_missing_value_passes(self) -> None:
        assert (
            _validate_enum(
                {"status": None},
                {"field": "status", "values": ["active"]},
            )
            is True
        )

    def test_empty_values_passes(self) -> None:
        # 空 values 视为不校验
        assert _validate_enum({"status": "anything"}, {"field": "status", "values": []}) is True

    def test_non_list_values_passes(self) -> None:
        assert (
            _validate_enum(
                {"status": "active"},
                {"field": "status", "values": "not-a-list"},
            )
            is True
        )

    def test_empty_field_passes(self) -> None:
        assert _validate_enum({"status": "active"}, {}) is True


# ----------------------------------------------------------------
# unique 校验器
# ----------------------------------------------------------------


class TestUnique:
    def test_first_seen_passes(self) -> None:
        seen: set[Any] = set()
        assert _validate_unique({"id": 1}, {"field": "id"}, seen) is True
        assert 1 in seen or ("1",) in seen or _hashable_in(seen, 1)

    def test_second_seen_fails(self) -> None:
        seen: set[Any] = set()
        _validate_unique({"id": 1}, {"field": "id"}, seen)
        assert _validate_unique({"id": 1}, {"field": "id"}, seen) is False

    def test_different_values_passes(self) -> None:
        seen: set[Any] = set()
        _validate_unique({"id": 1}, {"field": "id"}, seen)
        assert _validate_unique({"id": 2}, {"field": "id"}, seen) is True

    def test_missing_value_passes(self) -> None:
        seen: set[Any] = set()
        assert _validate_unique({"id": None}, {"field": "id"}, seen) is True

    def test_empty_field_passes(self) -> None:
        seen: set[Any] = set()
        assert _validate_unique({"id": 1}, {}, seen) is True

    def test_list_value_unique(self) -> None:
        """list 值转为可哈希键后判重."""
        seen: set[Any] = set()
        assert _validate_unique({"tags": [1, 2]}, {"field": "tags"}, seen) is True
        assert _validate_unique({"tags": [1, 2]}, {"field": "tags"}, seen) is False
        assert _validate_unique({"tags": [3, 4]}, {"field": "tags"}, seen) is True

    def test_dict_value_unique(self) -> None:
        seen: set[Any] = set()
        assert _validate_unique({"meta": {"k": "v"}}, {"field": "meta"}, seen) is True
        assert _validate_unique({"meta": {"k": "v"}}, {"field": "meta"}, seen) is False


def _hashable_in(seen: set[Any], value: Any) -> bool:
    """辅助：检查 value 是否在 seen 中（考虑可哈希包装）."""
    from apps.ingest.validation import _hashable_key

    return _hashable_key(value) in seen


# ----------------------------------------------------------------
# expression 校验器
# ----------------------------------------------------------------


class TestExpression:
    def test_true_expression_passes(self) -> None:
        assert _validate_expression({"age": 25}, {"field": "age", "expr": "value > 0"}) is True

    def test_false_expression_fails(self) -> None:
        assert _validate_expression({"age": -5}, {"field": "age", "expr": "value > 0"}) is False

    def test_missing_value_passes(self) -> None:
        assert _validate_expression({"age": None}, {"field": "age", "expr": "value > 0"}) is True

    def test_invalid_expression_fails(self) -> None:
        # 语法错误视为失败
        assert _validate_expression({"age": 25}, {"field": "age", "expr": "value >"}) is False

    def test_expression_with_item_var(self) -> None:
        # 引用 item 变量
        assert (
            _validate_expression(
                {"a": 10, "b": 20},
                {"field": "a", "expr": "value < item['b']"},
            )
            is True
        )

    def test_empty_expr_passes(self) -> None:
        # 空表达式视为不校验
        assert _validate_expression({"age": 25}, {"field": "age", "expr": ""}) is True

    def test_non_string_expr_passes(self) -> None:
        assert _validate_expression({"age": 25}, {"field": "age", "expr": 123}) is True

    def test_expression_uses_safe_builtins(self) -> None:
        # abs/min/max/len 等可用
        assert _validate_expression({"v": -5}, {"field": "v", "expr": "abs(value) == 5"}) is True

    def test_expression_blocks_dangerous_builtins(self) -> None:
        # __import__ 不可用，触发异常视为失败
        assert _validate_expression({"v": 1}, {"field": "v", "expr": "__import__('os')"}) is False

    def test_no_field_passes(self) -> None:
        # 无 field 时 value=None，但表达式仍可执行
        assert _validate_expression({}, {"expr": "True"}) is True

    def test_no_field_with_value_expr_fails(self) -> None:
        # 无 field 时 value=None，value > 0 失败
        assert _validate_expression({}, {"expr": "value > 0"}) is False


# ----------------------------------------------------------------
# _RuleStats
# ----------------------------------------------------------------


class TestRuleStats:
    def test_initial_state(self) -> None:
        stats = _RuleStats("name", "required")
        assert stats.field == "name"
        assert stats.op == "required"
        assert stats.total == 0
        assert stats.passed == 0
        assert stats.failed == 0
        assert stats.samples == []
        assert stats.pass_rate == 100.0

    def test_record_pass(self) -> None:
        stats = _RuleStats("name", "required")
        stats.record(True, "abc")
        assert stats.total == 1
        assert stats.passed == 1
        assert stats.failed == 0
        assert stats.samples == []
        assert stats.pass_rate == 100.0

    def test_record_fail(self) -> None:
        stats = _RuleStats("name", "required")
        stats.record(False, None, "缺失")
        assert stats.total == 1
        assert stats.passed == 0
        assert stats.failed == 1
        assert len(stats.samples) == 1
        assert stats.samples[0]["value"] is None
        assert stats.samples[0]["reason"] == "缺失"
        assert stats.pass_rate == 0.0

    def test_pass_rate_mixed(self) -> None:
        stats = _RuleStats("name", "required")
        stats.record(True, "a")
        stats.record(False, None)
        stats.record(True, "b")
        # 2/3 = 66.7
        assert stats.pass_rate == 66.7

    def test_samples_capped_at_max(self) -> None:
        stats = _RuleStats("name", "required")
        for i in range(MAX_SAMPLES_PER_RULE + 10):
            stats.record(False, f"v{i}")
        # 样本被截断到 MAX_SAMPLES_PER_RULE
        assert len(stats.samples) == MAX_SAMPLES_PER_RULE
        # total 与 failed 仍记录全部
        assert stats.total == MAX_SAMPLES_PER_RULE + 10
        assert stats.failed == MAX_SAMPLES_PER_RULE + 10

    def test_complex_value_coerced_to_json(self) -> None:
        stats = _RuleStats("tags", "required")
        stats.record(False, {"k": "v"}, "校验失败")
        # dict 值转为 JSON 字符串
        assert isinstance(stats.samples[0]["value"], str)
        assert "k" in stats.samples[0]["value"]


# ----------------------------------------------------------------
# ValidationPipeline 生命周期
# ----------------------------------------------------------------


class TestValidationPipelineEmptyConfig:
    """空配置透传不修改 item."""

    def test_empty_config_passthrough(self) -> None:
        pipeline = ValidationPipeline()
        pipeline.open_spider(_FakeSpider(validation_config={}))
        original = {"id": 1, "name": "abc"}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_none_config_passthrough(self) -> None:
        pipeline = ValidationPipeline()
        pipeline.open_spider(_FakeSpider(validation_config=None))
        original = {"id": 1}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_no_rules_passthrough(self) -> None:
        pipeline = ValidationPipeline()
        pipeline.open_spider(_FakeSpider(validation_config={"rules": []}))
        original = {"id": 1}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_non_dict_item_passthrough(self) -> None:
        pipeline = ValidationPipeline()
        pipeline.open_spider(_FakeSpider(validation_config={"rules": [{"field": "x", "op": "required"}]}))
        result = pipeline.process_item("not-a-dict", _FakeSpider())
        assert result == "not-a-dict"

    def test_no_validation_config_attr(self) -> None:
        """spider 无 validation_config 属性时不报错."""

        class BareSpider:
            pass

        pipeline = ValidationPipeline()
        pipeline.open_spider(BareSpider())
        assert pipeline._rules == []


class TestValidationPipelineRules:
    """校验规则按序执行，失败不丢弃."""

    def test_rules_applied_in_order(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    {"field": "name", "op": "required"},
                    {"field": "age", "op": "range", "min": 0, "max": 150},
                ]
            }
        )
        pipeline.open_spider(spider)
        result = pipeline.process_item({"name": "abc", "age": 25}, spider)
        # 失败也不丢弃
        assert result == {"name": "abc", "age": 25}

    def test_failure_does_not_drop_item(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    {"field": "name", "op": "required"},
                ]
            }
        )
        pipeline.open_spider(spider)
        # name 缺失，校验失败但不丢弃
        result = pipeline.process_item({"name": None, "age": 25}, spider)
        assert result == {"name": None, "age": 25}

    def test_unknown_op_filtered_out(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    {"field": "name", "op": "nonexistent_op"},
                    {"field": "name", "op": "required"},
                ]
            }
        )
        pipeline.open_spider(spider)
        # 未知 op 被过滤
        assert len(pipeline._rules) == 1
        result = pipeline.process_item({"name": "abc"}, spider)
        assert result == {"name": "abc"}

    def test_rules_not_a_list_skipped(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={"rules": "not-a-list"})
        pipeline.open_spider(spider)
        original = {"id": 1}
        result = pipeline.process_item(dict(original), spider)
        assert result == original

    def test_rule_not_a_dict_filtered(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    "not-a-dict",
                    {"field": "name", "op": "required"},
                ]
            }
        )
        pipeline.open_spider(spider)
        result = pipeline.process_item({"name": "abc"}, spider)
        assert result == {"name": "abc"}

    def test_does_not_mutate_input_item(self) -> None:
        """校验器不应修改原始 item."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={"rules": [{"field": "name", "op": "required"}]})
        pipeline.open_spider(spider)
        original = {"name": "abc"}
        pipeline.process_item(original, spider)
        # 原始 item 不被修改
        assert original == {"name": "abc"}

    def test_validator_exception_treated_as_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """校验器抛异常时视为失败，不中断主流程."""
        from apps.ingest import validation as validation_mod

        def _boom(_item: dict[str, Any], _rule: dict[str, Any]) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setitem(validation_mod._VALIDATORS, "required", _boom)
        pipeline = validation_mod.ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "name", "op": "required"}]},
            task_id=None,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 不应抛异常
        result = pipeline.process_item({"name": "abc"}, spider)
        assert result == {"name": "abc"}
        # 异常视为失败
        assert pipeline._stats.values.get("ingest_validation_failed", 0) == 1
        assert pipeline._stats.values.get("ingest_rows_invalid", 0) == 1


class TestValidationPipelineStats:
    """stats 收集."""

    def test_stats_collected_on_pass(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={"rules": [{"field": "name", "op": "required"}]})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"name": "abc"}, spider)
        assert pipeline._stats.values["ingest_validation_total"] == 1
        assert pipeline._stats.values["ingest_validation_passed"] == 1
        # failed 与 invalid_rows 仅在失败时 inc_value，无失败时不存在
        assert pipeline._stats.values.get("ingest_validation_failed", 0) == 0
        assert pipeline._stats.values.get("ingest_rows_invalid", 0) == 0

    def test_stats_collected_on_fail(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={"rules": [{"field": "name", "op": "required"}]})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"name": None}, spider)
        assert pipeline._stats.values["ingest_validation_total"] == 1
        assert pipeline._stats.values.get("ingest_validation_passed", 0) == 0
        assert pipeline._stats.values["ingest_validation_failed"] == 1
        assert pipeline._stats.values["ingest_rows_invalid"] == 1

    def test_invalid_rows_dedup_per_item(self) -> None:
        """同一 item 多条规则失败，invalid_rows 只累加 1."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    {"field": "name", "op": "required"},
                    {"field": "age", "op": "required"},
                ]
            }
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 两条规则都失败，invalid_rows 只 +1
        pipeline.process_item({"name": None, "age": None}, spider)
        assert pipeline._stats.values["ingest_validation_failed"] == 2
        assert pipeline._stats.values["ingest_rows_invalid"] == 1

    def test_close_spider_writes_final_stats(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "name", "op": "required"}]},
            task_id=None,  # 避免 close_spider 查询 DB
        )
        stats = _FakeStats()
        pipeline._stats = stats
        pipeline.open_spider(spider)
        pipeline.process_item({"name": "abc"}, spider)
        pipeline.process_item({"name": None}, spider)
        pipeline.close_spider(spider)
        # set_value 兜底覆盖最终值
        assert stats.values["ingest_validation_total"] == 2
        assert stats.values["ingest_validation_passed"] == 1
        assert stats.values["ingest_validation_failed"] == 1
        assert stats.values["ingest_rows_invalid"] == 1

    def test_close_spider_no_stats_noop(self) -> None:
        pipeline = ValidationPipeline()
        # 未设置 stats 时 close_spider 不报错
        pipeline.close_spider(_FakeSpider(task_id=None))

    def test_from_crawler_binds_stats(self) -> None:
        crawler = _FakeCrawler()
        pipeline = ValidationPipeline.from_crawler(crawler)
        assert pipeline._stats is crawler.stats


class TestValidationPipelineUnique:
    """unique 规则的批次内去重."""

    def test_unique_tracks_across_items(self) -> None:
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={"rules": [{"field": "id", "op": "unique"}]})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 第一次：通过
        pipeline.process_item({"id": 1}, spider)
        # 第二次：失败
        pipeline.process_item({"id": 1}, spider)
        # 第三次：不同 id 通过
        pipeline.process_item({"id": 2}, spider)
        stats = next(iter(pipeline._rule_stats.values()))
        assert stats.total == 3
        assert stats.passed == 2
        assert stats.failed == 1


class TestValidationPipelineCloseSpiderReports:
    """close_spider 写入 IngestQualityReport."""

    def test_close_spider_writes_quality_reports(self, db: Any, admin_user: Any) -> None:
        ds = DataSource.objects.create(
            name="ds_q",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-q",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        # 创建一条 IngestLog（close_spider 会关联到最新 log）
        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=3,
            rows_written=3,
            rows_skipped=0,
            started_at=timezone.now() - timedelta(seconds=10),
            finished_at=timezone.now(),
            duration_ms=10000,
        )

        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={
                "rules": [
                    {"field": "name", "op": "required"},
                    {"field": "age", "op": "range", "min": 0, "max": 150},
                ]
            },
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"name": "a", "age": 25}, spider)
        pipeline.process_item({"name": None, "age": 200}, spider)
        pipeline.process_item({"name": "b", "age": 30}, spider)
        pipeline.close_spider(spider)

        reports = list(IngestQualityReport.objects.filter(task=task).order_by("field"))
        # 2 条规则各产生一条报告
        assert len(reports) == 2
        # age:range 报告
        age_report = next(r for r in reports if r.field == "age")
        assert age_report.rule == "range"
        assert age_report.total_count == 3
        assert age_report.passed_count == 2
        assert age_report.failed_count == 1
        assert age_report.pass_rate == 66.7
        assert age_report.log_id == log.pk
        assert len(age_report.failure_samples) == 1
        # name:required 报告
        name_report = next(r for r in reports if r.field == "name")
        assert name_report.rule == "required"
        assert name_report.passed_count == 2
        assert name_report.failed_count == 1

    def test_close_spider_no_task_id_skips_report(self) -> None:
        """无 task_id 时仅写 stats，不创建报告."""
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

    def test_close_spider_no_log_skips_report(self, db: Any, admin_user: Any) -> None:
        """task 无 log 时不创建报告（仅记 warning）."""
        ds = DataSource.objects.create(
            name="ds_q2",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-q2",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "x", "op": "required"}]},
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"x": "a"}, spider)
        pipeline.close_spider(spider)
        # 无 log 不创建报告
        assert IngestQualityReport.objects.filter(task=task).count() == 0

    def test_close_spider_empty_rules_skips_report(self, db: Any, admin_user: Any) -> None:
        """空规则时不创建报告."""
        pipeline = ValidationPipeline()
        spider = _FakeSpider(validation_config={})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.close_spider(spider)
        assert IngestQualityReport.objects.count() == 0

    def test_close_spider_zero_total_not_written(self, db: Any, admin_user: Any) -> None:
        """规则 total_count=0 时不写入（避免无意义报告）."""
        ds = DataSource.objects.create(
            name="ds_q3",
            engine=EngineType.SQLITE,
            database=":memory:",
            created_by=admin_user,
        )
        task = IngestTask.objects.create(
            name="t-q3",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=ds,
            target_table="out",
        )
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=0,
            rows_written=0,
            rows_skipped=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=0,
        )
        pipeline = ValidationPipeline()
        spider = _FakeSpider(
            validation_config={"rules": [{"field": "x", "op": "required"}]},
            task_id=task.pk,
        )
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        # 不调用 process_item，total=0
        pipeline.close_spider(spider)
        assert IngestQualityReport.objects.filter(task=task).count() == 0


# ----------------------------------------------------------------
# IngestQualityReport 模型
# ----------------------------------------------------------------


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    return DataSource.objects.create(
        name="ds_model",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def task_with_log(db: Any, datasource: DataSource) -> tuple[IngestTask, IngestLog]:
    task = IngestTask.objects.create(
        name="t-model",
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


class TestIngestQualityReportModel:
    def test_str(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, log = task_with_log
        report = IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="required",
            total_count=10,
            passed_count=9,
            failed_count=1,
            pass_rate=90.0,
            failure_samples=[{"value": None, "reason": "缺失"}],
        )
        s = str(report)
        assert "t-model" in s
        assert "name" in s
        assert "required" in s
        assert "90.0%" in s

    def test_defaults(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, log = task_with_log
        report = IngestQualityReport.objects.create(task=task, log=log, field="x", rule="required")
        assert report.total_count == 0
        assert report.passed_count == 0
        assert report.failed_count == 0
        assert report.pass_rate == 100.0
        assert report.failure_samples == []

    def test_aggregate_summary_no_reports(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, _ = task_with_log
        summary = IngestQualityReport.aggregate_summary(task.pk)
        assert summary["total_rules"] == 0
        assert summary["avg_pass_rate"] == 0.0
        assert summary["worst_field"] == ""
        assert summary["worst_rule"] == ""
        assert summary["total_failures"] == 0
        assert summary["last_report_at"] is None

    def test_aggregate_summary_with_reports(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, log = task_with_log
        IngestQualityReport.objects.create(
            task=task,
            log=log,
            field="name",
            rule="required",
            total_count=10,
            passed_count=9,
            failed_count=1,
            pass_rate=90.0,
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
        summary = IngestQualityReport.aggregate_summary(task.pk)
        assert summary["total_rules"] == 2
        assert summary["avg_pass_rate"] == 70.0  # (90 + 50) / 2
        assert summary["worst_field"] == "age"
        assert summary["worst_rule"] == "range"
        assert summary["total_failures"] == 6
        assert summary["last_report_at"] is not None

    def test_cascade_delete_task(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, log = task_with_log
        IngestQualityReport.objects.create(task=task, log=log, field="x", rule="required")
        assert IngestQualityReport.objects.filter(task=task).count() == 1
        task.delete()
        assert IngestQualityReport.objects.filter(pk__in=[]).count() == 0
        # 任务删除后报告也被级联删除
        assert IngestQualityReport.objects.count() == 0

    def test_cascade_delete_log(self, db: Any, task_with_log: tuple[IngestTask, IngestLog]) -> None:
        task, log = task_with_log
        IngestQualityReport.objects.create(task=task, log=log, field="x", rule="required")
        log.delete()
        # 日志删除后报告也被级联删除
        assert IngestQualityReport.objects.count() == 0


# ----------------------------------------------------------------
# 质量报告 API
# ----------------------------------------------------------------


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class TestQualityReportAPI:
    def test_list_quality_reports_empty(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        task = IngestTask.objects.create(
            name="t-api",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-reports",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_quality_reports_with_data(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-api2",
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
            field="name",
            rule="required",
            total_count=5,
            passed_count=4,
            failed_count=1,
            pass_rate=80.0,
            failure_samples=[{"value": None, "reason": "缺失"}],
        )

        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-reports",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["field"] == "name"
        assert body[0]["rule"] == "required"
        assert body[0]["total_count"] == 5
        assert body[0]["passed_count"] == 4
        assert body[0]["failed_count"] == 1
        assert body[0]["pass_rate"] == 80.0
        assert body[0]["log_id"] == log.pk
        assert body[0]["task_id"] == task.pk
        assert len(body[0]["failure_samples"]) == 1

    def test_list_quality_reports_filter_by_log_id(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-api3",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        log1 = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=5,
            rows_written=5,
            rows_skipped=0,
            started_at=timezone.now() - timedelta(hours=2),
            finished_at=timezone.now() - timedelta(hours=2),
            duration_ms=100,
        )
        log2 = IngestLog.objects.create(
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
            log=log1,
            field="name",
            rule="required",
            total_count=5,
            passed_count=5,
            failed_count=0,
            pass_rate=100.0,
        )
        IngestQualityReport.objects.create(
            task=task,
            log=log2,
            field="age",
            rule="range",
            total_count=5,
            passed_count=3,
            failed_count=2,
            pass_rate=60.0,
        )

        # 不限定 log_id：返回全部
        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-reports",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        # 限定 log_id=log1
        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-reports?log_id={log1.pk}",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["field"] == "name"

    def test_list_quality_reports_task_not_found(self, db: Any, client: Client, admin_user: Any) -> None:
        resp = client.get(
            "/api/v1/ingest/tasks/99999/quality-reports",
            **_auth(admin_user),
        )
        assert resp.status_code == 404

    def test_get_quality_summary_no_reports(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-sum",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-summary",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task.pk
        assert body["total_rules"] == 0
        assert body["avg_pass_rate"] == 0.0
        assert body["worst_field"] == ""
        assert body["worst_rule"] == ""
        assert body["total_failures"] == 0
        assert body["last_report_at"] is None

    def test_get_quality_summary_with_reports(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
    ) -> None:
        task = IngestTask.objects.create(
            name="t-sum2",
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

        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-summary",
            **_auth(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_rules"] == 2
        assert body["avg_pass_rate"] == 65.0  # (80 + 50) / 2
        assert body["worst_field"] == "age"
        assert body["worst_rule"] == "range"
        assert body["total_failures"] == 7
        assert body["last_report_at"] is not None

    def test_quality_summary_task_not_found(self, db: Any, client: Client, admin_user: Any) -> None:
        resp = client.get(
            "/api/v1/ingest/tasks/99999/quality-summary",
            **_auth(admin_user),
        )
        assert resp.status_code == 404

    def test_regular_user_can_list_reports(
        self,
        db: Any,
        client: Client,
        regular_user: Any,
        datasource: DataSource,
    ) -> None:
        """普通用户可查看质量报告（与日志列表一致，非管理员专属）."""
        task = IngestTask.objects.create(
            name="t-viewer",
            source_type=SourceType.API,
            source_url="https://example.com",
            target_datasource=datasource,
            target_table="out",
        )
        resp = client.get(
            f"/api/v1/ingest/tasks/{task.pk}/quality-reports",
            **_auth(regular_user),
        )
        assert resp.status_code == 200
