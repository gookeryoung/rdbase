"""CleaningPipeline 与清洗器单元测试.

覆盖：
- 6 类清洗器（on_missing/cast_type/normalize/strip_html/enum_map）单独验证
- DedupTracker 内存模式与 fakeredis 模式
- CleaningPipeline 生命周期（open/process/close + from_crawler）
- 空配置透传、DropItem 传播、stats 收集
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.ingest.cleaning import (
    CleaningPipeline,
    DedupTracker,
    DropItem,
    _apply_cast_type,
    _apply_enum_map,
    _apply_normalize,
    _apply_on_missing,
    _apply_strip_html,
    _is_missing,
    _normalize_phone,
    _normalize_url,
    _parse_bool,
    _parse_datetime,
)
from apps.system import redis_client
from django.test import override_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------


class _FakeStats:
    """假 stats 收集器，同时支持 inc_value 与 set_value."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get_value(self, key: str, default: Any = 0) -> Any:
        return self.values.get(key, default)

    def inc_value(self, key: str, count: int = 1) -> None:
        self.values[key] = self.values.get(key, 0) + count


class _FakeCrawler:
    """假 crawler，提供 stats."""

    def __init__(self) -> None:
        self.stats = _FakeStats()


class _FakeSpider:
    """假 spider，携带 clean_config 与 task_id."""

    def __init__(
        self,
        *,
        clean_config: dict[str, Any] | None = None,
        task_id: int | None = 42,
    ) -> None:
        self.clean_config = clean_config or {}
        self.task_id = task_id


# ----------------------------------------------------------------
# _is_missing
# ----------------------------------------------------------------


class TestIsMissing:
    def test_none_is_missing(self) -> None:
        assert _is_missing(None) is True

    def test_empty_string_is_missing(self) -> None:
        assert _is_missing("") is True
        assert _is_missing("   ") is True

    def test_zero_is_not_missing(self) -> None:
        # 0 是有效值，不应被当作缺失
        assert _is_missing(0) is False

    def test_false_is_not_missing(self) -> None:
        assert _is_missing(False) is False

    def test_non_empty_string_is_not_missing(self) -> None:
        assert _is_missing("abc") is False


# ----------------------------------------------------------------
# on_missing
# ----------------------------------------------------------------


class TestOnMissing:
    def test_fill_default(self) -> None:
        item: dict[str, Any] = {"name": None}
        _apply_on_missing(item, {"field": "name", "strategy": "fill_default", "default": "N/A"})
        assert item["name"] == "N/A"

    def test_fill_default_no_default_uses_empty(self) -> None:
        item: dict[str, Any] = {"name": ""}
        _apply_on_missing(item, {"field": "name", "strategy": "fill_default"})
        assert item["name"] == ""

    def test_skip_raises_drop(self) -> None:
        item: dict[str, Any] = {"name": None}
        with pytest.raises(DropItem, match="strategy=skip"):
            _apply_on_missing(item, {"field": "name", "strategy": "skip"})

    def test_abort_raises_drop(self) -> None:
        item: dict[str, Any] = {"name": None}
        with pytest.raises(DropItem, match="strategy=abort"):
            _apply_on_missing(item, {"field": "name", "strategy": "abort"})

    def test_present_value_unchanged(self) -> None:
        item: dict[str, Any] = {"name": "abc"}
        _apply_on_missing(item, {"field": "name", "strategy": "fill_default", "default": "X"})
        assert item["name"] == "abc"

    def test_empty_field_skipped(self) -> None:
        item: dict[str, Any] = {"name": "abc"}
        # 无 field 不报错也不修改
        _apply_on_missing(item, {"strategy": "fill_default"})
        assert item == {"name": "abc"}

    def test_default_strategy_is_fill_default(self) -> None:
        item: dict[str, Any] = {"name": None}
        # 未指定 strategy 时默认 fill_default
        _apply_on_missing(item, {"field": "name", "default": "default-val"})
        assert item["name"] == "default-val"


# ----------------------------------------------------------------
# cast_type
# ----------------------------------------------------------------


class TestCastType:
    def test_cast_int(self) -> None:
        item: dict[str, Any] = {"age": "42"}
        _apply_cast_type(item, {"field": "age", "cast_type": "int"})
        assert item["age"] == 42

    def test_cast_float(self) -> None:
        item: dict[str, Any] = {"score": "3.14"}
        _apply_cast_type(item, {"field": "score", "cast_type": "float"})
        assert item["score"] == 3.14

    def test_cast_bool_true_variants(self) -> None:
        for v in ("true", "1", "yes", "Y", "on", True):
            item: dict[str, Any] = {"flag": v}
            _apply_cast_type(item, {"field": "flag", "cast_type": "bool"})
            assert item["flag"] is True

    def test_cast_bool_false_variants(self) -> None:
        for v in ("false", "0", "no", "n", "off", False):
            item: dict[str, Any] = {"flag": v}
            _apply_cast_type(item, {"field": "flag", "cast_type": "bool"})
            assert item["flag"] is False

    def test_cast_datetime_iso(self) -> None:
        item: dict[str, Any] = {"ts": "2026-08-10T12:30:00"}
        _apply_cast_type(item, {"field": "ts", "cast_type": "datetime"})
        assert item["ts"] == "2026-08-10T12:30:00"

    def test_cast_datetime_with_format(self) -> None:
        item: dict[str, Any] = {"ts": "2026/08/10 12:30:00"}
        _apply_cast_type(
            item,
            {"field": "ts", "cast_type": "datetime", "datetime_format": "%Y/%m/%d %H:%M:%S"},
        )
        assert item["ts"] == "2026-08-10T12:30:00"

    def test_cast_json_object(self) -> None:
        item: dict[str, Any] = {"meta": '{"k": "v"}'}
        _apply_cast_type(item, {"field": "meta", "cast_type": "json"})
        assert item["meta"] == {"k": "v"}

    def test_cast_json_array(self) -> None:
        item: dict[str, Any] = {"tags": "[1, 2, 3]"}
        _apply_cast_type(item, {"field": "tags", "cast_type": "json"})
        assert item["tags"] == [1, 2, 3]

    def test_cast_int_failure_raises_drop(self) -> None:
        item: dict[str, Any] = {"age": "abc"}
        with pytest.raises(DropItem, match="类型转换"):
            _apply_cast_type(item, {"field": "age", "cast_type": "int"})

    def test_cast_unknown_type_skips_silently(self) -> None:
        item: dict[str, Any] = {"age": "abc"}
        # 未知 cast_type 不报错也不修改
        _apply_cast_type(item, {"field": "age", "cast_type": "unknown"})
        assert item["age"] == "abc"

    def test_cast_missing_value_skips(self) -> None:
        item: dict[str, Any] = {"age": None}
        _apply_cast_type(item, {"field": "age", "cast_type": "int"})
        assert item["age"] is None

    def test_cast_field_absent_skips(self) -> None:
        item: dict[str, Any] = {"other": 1}
        _apply_cast_type(item, {"field": "age", "cast_type": "int"})
        assert item == {"other": 1}


class TestParseBool:
    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="无法解析为布尔值"):
            _parse_bool("maybe")


class TestParseDatetime:
    def test_passes_through_datetime(self) -> None:
        from datetime import datetime

        dt = datetime(2026, 8, 10, 12, 0, 0)
        assert _parse_datetime(dt, None) == "2026-08-10T12:00:00"


# ----------------------------------------------------------------
# normalize
# ----------------------------------------------------------------


class TestNormalize:
    def test_trim(self) -> None:
        item: dict[str, Any] = {"name": "  abc  "}
        _apply_normalize(item, {"field": "name", "normalizer": "trim"})
        assert item["name"] == "abc"

    def test_upper(self) -> None:
        item: dict[str, Any] = {"name": "  abc  "}
        _apply_normalize(item, {"field": "name", "normalizer": "upper"})
        assert item["name"] == "ABC"

    def test_lower(self) -> None:
        item: dict[str, Any] = {"name": "  ABC  "}
        _apply_normalize(item, {"field": "name", "normalizer": "lower"})
        assert item["name"] == "abc"

    def test_phone(self) -> None:
        item: dict[str, Any] = {"phone": "  +86 (010) 123-45678  "}
        _apply_normalize(item, {"field": "phone", "normalizer": "phone"})
        assert item["phone"] == "+8601012345678"

    def test_phone_no_plus(self) -> None:
        item: dict[str, Any] = {"phone": "010-1234-5678"}
        _apply_normalize(item, {"field": "phone", "normalizer": "phone"})
        assert item["phone"] == "01012345678"

    def test_email(self) -> None:
        item: dict[str, Any] = {"email": "  Foo@Example.COM  "}
        _apply_normalize(item, {"field": "email", "normalizer": "email"})
        assert item["email"] == "foo@example.com"

    def test_url_strips_trailing_slash(self) -> None:
        item: dict[str, Any] = {"url": "HTTPS://Example.com/Path/"}
        _apply_normalize(item, {"field": "url", "normalizer": "url"})
        assert item["url"] == "https://example.com/Path"

    def test_url_preserves_query(self) -> None:
        item: dict[str, Any] = {"url": "https://example.com/search?q=hello"}
        _apply_normalize(item, {"field": "url", "normalizer": "url"})
        assert item["url"] == "https://example.com/search?q=hello"

    def test_date_iso(self) -> None:
        item: dict[str, Any] = {"date": "2026-08-10"}
        _apply_normalize(item, {"field": "date", "normalizer": "date"})
        assert item["date"] == "2026-08-10"

    def test_date_slash_format(self) -> None:
        item: dict[str, Any] = {"date": "2026/08/10"}
        _apply_normalize(item, {"field": "date", "normalizer": "date"})
        assert item["date"] == "2026-08-10"

    def test_date_unparseable_unchanged(self) -> None:
        item: dict[str, Any] = {"date": "Aug 10, 2026"}
        _apply_normalize(item, {"field": "date", "normalizer": "date"})
        assert item["date"] == "Aug 10, 2026"

    def test_unknown_normalizer_skips(self) -> None:
        item: dict[str, Any] = {"name": "abc"}
        _apply_normalize(item, {"field": "name", "normalizer": "unknown"})
        assert item["name"] == "abc"

    def test_non_string_value_unchanged(self) -> None:
        item: dict[str, Any] = {"age": 42}
        _apply_normalize(item, {"field": "age", "normalizer": "trim"})
        assert item["age"] == 42

    def test_field_absent_skips(self) -> None:
        item: dict[str, Any] = {"other": 1}
        _apply_normalize(item, {"field": "name", "normalizer": "trim"})
        assert item == {"other": 1}


class TestNormalizeHelpers:
    def test_phone_empty(self) -> None:
        assert _normalize_phone("") == ""

    def test_url_empty(self) -> None:
        assert _normalize_url("") == ""

    def test_url_root_path(self) -> None:
        # 末尾斜杠去掉后 path 为空，应回退为 '/'
        assert _normalize_url("https://example.com/") == "https://example.com/"


# ----------------------------------------------------------------
# strip_html
# ----------------------------------------------------------------


class TestStripHtml:
    def test_strips_tags(self) -> None:
        item: dict[str, Any] = {"desc": "<p>Hello <b>World</b></p>"}
        _apply_strip_html(item, {"field": "desc"})
        assert item["desc"] == "Hello World"

    def test_collapses_whitespace(self) -> None:
        item: dict[str, Any] = {"desc": "<div>  Line 1  <br>  Line 2  </div>"}
        _apply_strip_html(item, {"field": "desc"})
        assert item["desc"] == "Line 1 Line 2"

    def test_field_absent_skips(self) -> None:
        item: dict[str, Any] = {"other": 1}
        _apply_strip_html(item, {"field": "desc"})
        assert item == {"other": 1}

    def test_non_string_unchanged(self) -> None:
        item: dict[str, Any] = {"desc": 42}
        _apply_strip_html(item, {"field": "desc"})
        assert item["desc"] == 42


# ----------------------------------------------------------------
# enum_map
# ----------------------------------------------------------------


class TestEnumMap:
    def test_maps_known_value(self) -> None:
        item: dict[str, Any] = {"status": "1"}
        _apply_enum_map(item, {"field": "status", "mapping": {"1": "active", "0": "inactive"}})
        assert item["status"] == "active"

    def test_maps_int_value_via_str_key(self) -> None:
        item: dict[str, Any] = {"status": 1}
        _apply_enum_map(item, {"field": "status", "mapping": {"1": "active"}})
        assert item["status"] == "active"

    def test_default_when_unmatched(self) -> None:
        item: dict[str, Any] = {"status": "unknown"}
        _apply_enum_map(
            item,
            {"field": "status", "mapping": {"1": "active"}, "default": "unknown-status"},
        )
        assert item["status"] == "unknown-status"

    def test_no_default_unchanged(self) -> None:
        item: dict[str, Any] = {"status": "unknown"}
        _apply_enum_map(item, {"field": "status", "mapping": {"1": "active"}})
        assert item["status"] == "unknown"

    def test_non_dict_mapping_skips(self) -> None:
        item: dict[str, Any] = {"status": "1"}
        _apply_enum_map(item, {"field": "status", "mapping": "not-a-dict"})
        assert item["status"] == "1"

    def test_field_absent_skips(self) -> None:
        item: dict[str, Any] = {"other": 1}
        _apply_enum_map(item, {"field": "status", "mapping": {"1": "active"}})
        assert item == {"other": 1}


# ----------------------------------------------------------------
# DedupTracker
# ----------------------------------------------------------------


class TestDedupTrackerMemory:
    """无 Redis 时降级为内存 set."""

    def test_memory_first_seen_not_duplicate(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("ns1", ["id"])
            assert tracker.is_duplicate({"id": 1, "name": "a"}) is False

    def test_memory_second_seen_is_duplicate(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("ns2", ["id"])
            tracker.is_duplicate({"id": 1, "name": "a"})
            assert tracker.is_duplicate({"id": 1, "name": "b"}) is True

    def test_memory_different_id_not_duplicate(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("ns3", ["id"])
            tracker.is_duplicate({"id": 1})
            assert tracker.is_duplicate({"id": 2}) is False

    def test_memory_no_fields_uses_all_keys(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("ns4", [])
            tracker.is_duplicate({"id": 1, "name": "a"})
            # 同字段集合视为重复（顺序无关，因 keys 排序）
            assert tracker.is_duplicate({"name": "a", "id": 1}) is True

    def test_memory_missing_field_treated_as_empty(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("ns5", ["id"])
            # 缺 id 字段视为空字符串指纹
            assert tracker.is_duplicate({"name": "a"}) is False
            # 同样缺 id 视为重复
            assert tracker.is_duplicate({"name": "b"}) is True


class TestDedupTrackerRedis:
    """启用 fakeredis 时使用 Redis SET 共享."""

    def test_redis_first_seen_not_duplicate(self) -> None:
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("rns1", ["id"], ttl_hours=1)
            assert tracker.is_duplicate({"id": 1}) is False

    def test_redis_second_seen_is_duplicate(self) -> None:
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker = DedupTracker("rns2", ["id"], ttl_hours=1)
            tracker.is_duplicate({"id": 1})
            assert tracker.is_duplicate({"id": 1}) is True

    def test_redis_different_namespace_not_duplicate(self) -> None:
        """不同命名空间互不干扰."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            redis_client.reset_redis_client()
            tracker_a = DedupTracker("ns_a", ["id"], ttl_hours=1)
            tracker_b = DedupTracker("ns_b", ["id"], ttl_hours=1)
            tracker_a.is_duplicate({"id": 1})
            assert tracker_b.is_duplicate({"id": 1}) is False

    def test_redis_ttl_zero_no_expire(self) -> None:
        """ttl_hours=0 时不应调用 expire."""
        with override_settings(REDIS_FAKE=True, REDIS_URL=""):
            redis_client.reset_redis_client()
            client = redis_client.get_redis()
            assert client is not None
            tracker = DedupTracker("rns_ttl0", ["id"], ttl_hours=0)
            tracker.is_duplicate({"id": 1})
            # 验证 key 存在但无 TTL（expire 未调用，ttl 为 -1 表示无过期）
            ttl = client.ttl("ingest:dedup:rns_ttl0")
            assert ttl == -1


# ----------------------------------------------------------------
# CleaningPipeline 生命周期
# ----------------------------------------------------------------


class TestCleaningPipelineEmptyConfig:
    """空配置透传不修改 item."""

    def test_empty_config_passthrough(self) -> None:
        pipeline = CleaningPipeline()
        pipeline.open_spider(_FakeSpider(clean_config={}))
        original = {"id": 1, "name": "  abc  "}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_none_config_passthrough(self) -> None:
        pipeline = CleaningPipeline()
        pipeline.open_spider(_FakeSpider(clean_config=None))
        original = {"id": 1}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_no_rules_only_dedup_passthrough_when_no_dedup(self) -> None:
        pipeline = CleaningPipeline()
        pipeline.open_spider(_FakeSpider(clean_config={"rules": []}))
        original = {"id": 1}
        result = pipeline.process_item(dict(original), _FakeSpider())
        assert result == original

    def test_non_dict_item_passthrough(self) -> None:
        pipeline = CleaningPipeline()
        pipeline.open_spider(_FakeSpider(clean_config={"rules": [{"op": "on_missing", "field": "x"}]}))
        # 非 dict item 直接透传
        result = pipeline.process_item("not-a-dict", _FakeSpider())
        assert result == "not-a-dict"


class TestCleaningPipelineRules:
    """清洗规则按序执行."""

    def test_rules_applied_in_order(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(
            clean_config={
                "rules": [
                    {"field": "name", "op": "normalize", "normalizer": "trim"},
                    {"field": "name", "op": "normalize", "normalizer": "upper"},
                ]
            }
        )
        pipeline.open_spider(spider)
        result = pipeline.process_item({"name": "  abc  "}, spider)
        assert result == {"name": "ABC"}

    def test_rule_drop_propagates(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(
            clean_config={
                "rules": [
                    {"field": "name", "op": "on_missing", "strategy": "skip"},
                ]
            }
        )
        pipeline.open_spider(spider)
        with pytest.raises(DropItem, match="strategy=skip"):
            pipeline.process_item({"name": None}, spider)

    def test_unknown_op_filtered_out(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(
            clean_config={
                "rules": [
                    {"field": "name", "op": "nonexistent_op"},
                    {"field": "name", "op": "normalize", "normalizer": "upper"},
                ]
            }
        )
        pipeline.open_spider(spider)
        # 未知 op 被过滤掉，已知 op 仍执行
        result = pipeline.process_item({"name": "abc"}, spider)
        assert result == {"name": "ABC"}

    def test_rules_not_a_list_skipped(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"rules": "not-a-list"})
        pipeline.open_spider(spider)
        original = {"id": 1}
        result = pipeline.process_item(dict(original), spider)
        assert result == original

    def test_rule_not_a_dict_filtered(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(
            clean_config={
                "rules": [
                    "not-a-dict",
                    {"field": "name", "op": "normalize", "normalizer": "upper"},
                ]
            }
        )
        pipeline.open_spider(spider)
        result = pipeline.process_item({"name": "abc"}, spider)
        assert result == {"name": "ABC"}

    def test_does_not_mutate_input_item(self) -> None:
        """清洗器应返回新 dict，不修改原始 item."""
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"rules": [{"field": "name", "op": "normalize", "normalizer": "upper"}]})
        pipeline.open_spider(spider)
        original = {"name": "abc"}
        pipeline.process_item(original, spider)
        # 原始 item 不被修改
        assert original == {"name": "abc"}


class TestCleaningPipelineDedup:
    """清洗 + 去重组合."""

    def test_dedup_drops_second_occurrence(self) -> None:
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            pipeline = CleaningPipeline()
            spider = _FakeSpider(
                clean_config={
                    "dedup": {"enabled": True, "fields": ["id"], "ttl_hours": 24},
                }
            )
            pipeline.open_spider(spider)
            # 首次：保留
            first = pipeline.process_item({"id": 1, "name": "a"}, spider)
            assert first == {"id": 1, "name": "a"}
            # 第二次：丢弃
            with pytest.raises(DropItem, match="去重命中"):
                pipeline.process_item({"id": 1, "name": "b"}, spider)

    def test_dedup_disabled_no_drop(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"dedup": {"enabled": False, "fields": ["id"]}})
        pipeline.open_spider(spider)
        pipeline.process_item({"id": 1}, spider)
        # 不丢弃第二次
        result = pipeline.process_item({"id": 1}, spider)
        assert result == {"id": 1}

    def test_dedup_not_dict_skipped(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"dedup": "not-a-dict"})
        pipeline.open_spider(spider)
        # 无效 dedup 配置不启用去重
        result = pipeline.process_item({"id": 1}, spider)
        assert result == {"id": 1}


class TestCleaningPipelineStats:
    """stats 收集."""

    def test_cleaned_count_incremented(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"rules": [{"field": "name", "op": "normalize", "normalizer": "trim"}]})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        pipeline.process_item({"name": "  abc  "}, spider)
        pipeline.process_item({"name": "  def  "}, spider)
        assert pipeline._stats.values["ingest_rows_cleaned"] == 2

    def test_dropped_count_incremented_on_drop(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"rules": [{"field": "name", "op": "on_missing", "strategy": "skip"}]})
        pipeline._stats = _FakeStats()
        pipeline.open_spider(spider)
        with pytest.raises(DropItem):
            pipeline.process_item({"name": None}, spider)
        assert pipeline._stats.values["ingest_rows_dropped"] == 1

    def test_dedup_drop_increments_stats(self) -> None:
        """去重命中时 stats.dropped 也应累加."""
        with override_settings(REDIS_FAKE=False, REDIS_URL=""):
            redis_client.reset_redis_client()
            pipeline = CleaningPipeline()
            spider = _FakeSpider(clean_config={"dedup": {"enabled": True, "fields": ["id"], "ttl_hours": 24}})
            pipeline._stats = _FakeStats()
            pipeline.open_spider(spider)
            pipeline.process_item({"id": 1}, spider)
            with pytest.raises(DropItem, match="去重命中"):
                pipeline.process_item({"id": 1}, spider)
            assert pipeline._stats.values["ingest_rows_dropped"] == 1
            assert pipeline._stats.values["ingest_rows_cleaned"] == 1

    def test_close_spider_writes_final_stats(self) -> None:
        pipeline = CleaningPipeline()
        spider = _FakeSpider(clean_config={"rules": [{"field": "name", "op": "normalize", "normalizer": "trim"}]})
        stats = _FakeStats()
        pipeline._stats = stats
        pipeline.open_spider(spider)
        pipeline.process_item({"name": "  abc  "}, spider)
        pipeline.close_spider(spider)
        # set_value 兜底覆盖（与 inc_value 累加结果一致）
        assert stats.values["ingest_rows_cleaned"] == 1
        assert stats.values["ingest_rows_dropped"] == 0

    def test_close_spider_no_stats_noop(self) -> None:
        pipeline = CleaningPipeline()
        # 未设置 stats 时 close_spider 不报错
        pipeline.close_spider(_FakeSpider())

    def test_from_crawler_binds_stats(self) -> None:
        crawler = _FakeCrawler()
        pipeline = CleaningPipeline.from_crawler(crawler)
        assert pipeline._stats is crawler.stats


class TestCleaningPipelineOpenSpider:
    """open_spider 边界."""

    def test_no_clean_config_attr(self) -> None:
        """spider 无 clean_config 属性时不报错."""

        class BareSpider:
            pass

        pipeline = CleaningPipeline()
        pipeline.open_spider(BareSpider())
        assert pipeline._rules == []
        assert pipeline._dedup_tracker is None


# ----------------------------------------------------------------
# 集成测试：CleaningPipeline + FieldMappingPipeline 协作
# ----------------------------------------------------------------


def _make_engine() -> Engine:
    """创建 SQLite 内存引擎."""
    return create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _create_target_table(engine: Engine) -> None:
    """创建测试目标表."""
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE target (id INTEGER PRIMARY KEY, name TEXT)"))


class TestIntegrationWithFieldMapping:
    """清洗 + 字段映射协作：清洗后字段名匹配映射."""

    def test_clean_then_map_writes_to_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """清洗后的 item 经字段映射写入目标表."""
        from apps.ingest.pipelines import FieldMappingPipeline

        engine = _make_engine()
        _create_target_table(engine)
        fake_ds = type("DS", (), {"pk": 1})()

        def _fake_get_engine(_ds: Any) -> Engine:
            return engine

        def _fake_ds_get(**_kw: Any) -> Any:
            return fake_ds

        monkeypatch.setattr("apps.ingest.pipelines.get_engine", _fake_get_engine)
        monkeypatch.setattr(
            "apps.ingest.pipelines.DataSource",
            type(
                "DS",
                (),
                {
                    "objects": type("M", (), {"get": staticmethod(_fake_ds_get)})(),
                    "DoesNotExist": Exception,
                },
            ),
        )

        # 清洗 pipeline：trim name + cast id to int
        clean_pipeline = CleaningPipeline()
        clean_pipeline._stats = _FakeStats()
        clean_pipeline.open_spider(
            _FakeSpider(
                clean_config={
                    "rules": [
                        {"field": "id", "op": "cast_type", "cast_type": "int"},
                        {"field": "name", "op": "normalize", "normalizer": "trim"},
                    ]
                }
            )
        )

        # 字段映射 pipeline
        mappings = [
            {"source_field": "id", "target_field": "id", "mapping_type": "direct", "fixed_value": "", "is_pk": True},
            {
                "source_field": "name",
                "target_field": "name",
                "mapping_type": "direct",
                "fixed_value": "",
                "is_pk": False,
            },
        ]

        class _MapSpider:
            pass

        map_spider = _MapSpider()
        map_spider.target_datasource_id = 1
        map_spider.target_table = "target"
        map_spider.conflict_strategy = "upsert"
        map_spider.batch_size = 500
        map_spider.mappings = mappings

        map_pipeline = FieldMappingPipeline()
        map_pipeline._stats = _FakeStats()
        map_pipeline.open_spider(map_spider)

        # 原始 item：字符串 id + 带空白 name
        raw_item = {"id": "42", "name": "  alice  "}
        cleaned = clean_pipeline.process_item(raw_item, _FakeSpider())
        map_pipeline.process_item(cleaned, map_spider)
        map_pipeline.close_spider(map_spider)

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM target")).fetchall()
        assert rows == [(42, "alice")]
