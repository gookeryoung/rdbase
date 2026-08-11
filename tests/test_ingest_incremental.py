"""增量爬取策略测试（iter-54 P8-Q4）.

覆盖三种增量策略：

- API_UPDATED_AT（ApiIngestSpider）：``last_sync_at`` 作为查询参数注入 URL
- HTML_FINGERPRINT（HtmlIngestSpider）：页面内容 SHA-256 指纹比对，命中跳过
- DB_TIMESTAMP（DatabaseIngestSpider）：已在 test_ingest_database_spider.py 覆盖
- ``_format_last_sync``：ISO / strftime 格式化与异常回退
- ``_save_html_fingerprint``：指纹持久化到 task.incremental_config
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest
from apps.datasources.models import DataSource, EngineType
from apps.ingest.engine import _save_html_fingerprint
from apps.ingest.models import IngestTask, SourceType
from apps.ingest.spiders.api_spider import ApiIngestSpider
from apps.ingest.spiders.html_spider import _STAT_HTML_FINGERPRINT, HtmlIngestSpider
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_incremental",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


# ================================================================
# API_UPDATED_AT 策略
# ================================================================


class TestApiInjectUpdatedParam:
    """``_inject_updated_param`` URL 参数注入测试."""

    def test_no_last_sync_returns_url_unchanged(self) -> None:
        """首次执行（无 last_sync_at）应原样返回 URL."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={},
            incremental_config={"strategy": "api_updated_at"},
        )
        assert spider._inject_updated_param("http://x/api") == "http://x/api"

    def test_iso_format_default(self) -> None:
        """默认 format=iso 应原样追加 last_sync_at."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15T00:00:00"},
            incremental_config={"strategy": "api_updated_at"},
        )
        url = spider._inject_updated_param("http://x/api")
        assert "updated_since=2026-01-15T00%3A00%3A00" in url

    def test_custom_param_name(self) -> None:
        """自定义 param_name 时应使用该名称."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15"},
            incremental_config={
                "strategy": "api_updated_at",
                "param_name": "since",
            },
        )
        url = spider._inject_updated_param("http://x/api")
        assert "since=2026-01-15" in url

    def test_strftime_format(self) -> None:
        """strftime 模式应正确格式化."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15T10:30:00"},
            incremental_config={
                "strategy": "api_updated_at",
                "format": "%Y-%m-%d",
            },
        )
        url = spider._inject_updated_param("http://x/api")
        assert "updated_since=2026-01-15" in url

    def test_url_with_existing_query(self) -> None:
        """已有查询参数的 URL 应合并而非覆盖."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15"},
            incremental_config={"strategy": "api_updated_at"},
        )
        url = spider._inject_updated_param("http://x/api?page=1&size=10")
        assert "page=1" in url
        assert "size=10" in url
        assert "updated_since=2026-01-15" in url

    def test_no_strategy_returns_url_unchanged(self) -> None:
        """无增量策略时 start_requests 不应修改 URL."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15"},
            incremental_config={"strategy": "none"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert "updated_since" not in requests[0].url

    def test_with_strategy_injects_param(self) -> None:
        """有增量策略时 start_requests 应注入参数."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"__last_sync_at__": "2026-01-15"},
            incremental_config={"strategy": "api_updated_at"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert "updated_since=2026-01-15" in requests[0].url


class TestFormatLastSync:
    """``_format_last_sync`` 格式化测试."""

    def test_iso_returns_as_is(self) -> None:
        assert ApiIngestSpider._format_last_sync("2026-01-15T00:00:00", "iso") == "2026-01-15T00:00:00"

    def test_strftime_format(self) -> None:
        result = ApiIngestSpider._format_last_sync("2026-01-15T10:30:00", "%Y-%m-%d")
        assert result == "2026-01-15"

    def test_invalid_input_falls_back_to_iso(self) -> None:
        """非法时间字符串应回退原样返回."""
        result = ApiIngestSpider._format_last_sync("not-a-date", "%Y-%m-%d")
        assert result == "not-a-date"

    def test_none_input_falls_back(self) -> None:
        result = ApiIngestSpider._format_last_sync("invalid", "%Y-%m-%d")
        assert result == "invalid"


# ================================================================
# HTML_FINGERPRINT 策略
# ================================================================


def _make_response(url: str, body: str) -> TextResponse:
    """构造 Scrapy TextResponse."""
    request = Request(url=url)
    return TextResponse(url=url, body=body.encode("utf-8"), encoding="utf-8", request=request)


def _make_crawler_mock() -> SimpleNamespace:
    """构造 crawler.stats mock（收集 set_value 调用）."""
    values: dict[str, Any] = {}

    class _Stats:
        def set_value(self, key: str, value: Any) -> None:
            values[key] = value

        def get_value(self, key: str, default: Any = None) -> Any:
            return values.get(key, default)

    crawler = SimpleNamespace()
    crawler.stats = _Stats()
    # 把 values 挂在 crawler 上便于测试断言
    crawler._test_values = values  # type: ignore[attr-defined]
    return crawler


_TABLE_HTML = """
<html><body>
<table>
  <tbody>
    <tr><td>1</td><td>alice</td></tr>
    <tr><td>2</td><td>bob</td></tr>
  </tbody>
</table>
</body></html>
"""


class TestHtmlFingerprint:
    """HTML_FINGERPRINT 增量策略测试."""

    def test_first_run_sets_fingerprint(self) -> None:
        """首次执行（无存储指纹）应正常爬取并记录新指纹到 stats."""
        crawler = _make_crawler_mock()
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)", "name": "td:nth-child(2)"},
            },
            incremental_config={"strategy": "html_fingerprint"},
        )
        spider.crawler = crawler  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        items = [it for it in spider.parse(response) if isinstance(it, dict)]
        assert len(items) == 2
        expected_fp = hashlib.sha256(_TABLE_HTML.encode("utf-8")).hexdigest()
        assert crawler._test_values[_STAT_HTML_FINGERPRINT] == expected_fp

    def test_same_fingerprint_skips(self) -> None:
        """指纹一致时应跳过爬取（不产出 item，不翻页）."""
        fingerprint = hashlib.sha256(_TABLE_HTML.encode("utf-8")).hexdigest()
        crawler = _make_crawler_mock()
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
            incremental_config={
                "strategy": "html_fingerprint",
                "_last_fingerprint": fingerprint,
            },
        )
        spider.crawler = crawler  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        results = list(spider.parse(response))
        assert results == []
        # 指纹一致时不更新 stats
        assert _STAT_HTML_FINGERPRINT not in crawler._test_values

    def test_different_fingerprint_proceeds(self) -> None:
        """指纹不一致时应正常爬取并更新指纹."""
        crawler = _make_crawler_mock()
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
            incremental_config={
                "strategy": "html_fingerprint",
                "_last_fingerprint": "old_fingerprint_value",
            },
        )
        spider.crawler = crawler  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        items = [it for it in spider.parse(response) if isinstance(it, dict)]
        assert len(items) == 2
        expected_fp = hashlib.sha256(_TABLE_HTML.encode("utf-8")).hexdigest()
        assert crawler._test_values[_STAT_HTML_FINGERPRINT] == expected_fp

    def test_no_strategy_does_not_check_fingerprint(self) -> None:
        """无增量策略时不检查指纹."""
        crawler = _make_crawler_mock()
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
            incremental_config={"strategy": "none"},
        )
        spider.crawler = crawler  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        items = [it for it in spider.parse(response) if isinstance(it, dict)]
        assert len(items) == 2
        # 不应记录指纹
        assert _STAT_HTML_FINGERPRINT not in crawler._test_values

    def test_page2_skips_fingerprint_check(self) -> None:
        """翻页（page>=2）不检查指纹."""
        crawler = _make_crawler_mock()
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
            incremental_config={
                "strategy": "html_fingerprint",
                "_last_fingerprint": hashlib.sha256(_TABLE_HTML.encode("utf-8")).hexdigest(),
            },
        )
        spider.crawler = crawler  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        # page=2 时不应跳过
        items = [it for it in spider.parse(response, page=2) if isinstance(it, dict)]
        assert len(items) == 2

    def test_no_crawler_does_not_crash(self) -> None:
        """crawler 为 None 时不应崩溃（仅不记录指纹）."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
            incremental_config={"strategy": "html_fingerprint"},
        )
        spider.crawler = None  # type: ignore[assignment]
        response = _make_response("http://x", _TABLE_HTML)
        items = [it for it in spider.parse(response) if isinstance(it, dict)]
        assert len(items) == 2


# ================================================================
# _save_html_fingerprint 持久化
# ================================================================


class TestSaveHtmlFingerprint:
    """``_save_html_fingerprint`` 指纹持久化测试."""

    def test_persists_fingerprint(self, db: Any, datasource: DataSource) -> None:
        """应将指纹写入 task.incremental_config._last_fingerprint."""
        task = IngestTask.objects.create(
            name="fp_task",
            source_type=SourceType.HTML,
            source_url="http://x",
            target_datasource=datasource,
            target_table="out",
            incremental_config={"strategy": "html_fingerprint"},
        )
        _save_html_fingerprint(task, "new_fp_value")
        task.refresh_from_db()
        assert task.incremental_config["_last_fingerprint"] == "new_fp_value"
        assert task.incremental_config["strategy"] == "html_fingerprint"

    def test_preserves_other_config_keys(self, db: Any, datasource: DataSource) -> None:
        """应保留 incremental_config 中的其他键."""
        task = IngestTask.objects.create(
            name="fp_task_preserve",
            source_type=SourceType.HTML,
            source_url="http://x",
            target_datasource=datasource,
            target_table="out",
            incremental_config={
                "strategy": "html_fingerprint",
                "_last_fingerprint": "old",
            },
        )
        _save_html_fingerprint(task, "updated")
        task.refresh_from_db()
        assert task.incremental_config["_last_fingerprint"] == "updated"
        assert task.incremental_config["strategy"] == "html_fingerprint"

    def test_syncs_local_task_instance(self, db: Any, datasource: DataSource) -> None:
        """应同步本地 task 实例的 incremental_config."""
        task = IngestTask.objects.create(
            name="fp_task_sync",
            source_type=SourceType.HTML,
            source_url="http://x",
            target_datasource=datasource,
            target_table="out",
            incremental_config={"strategy": "html_fingerprint"},
        )
        _save_html_fingerprint(task, "synced_fp")
        # 本地实例也应同步更新
        assert task.incremental_config["_last_fingerprint"] == "synced_fp"
