"""ingest 端到端集成测试.

启动本地 HTTP server 返回 JSON 数据，构造 Scrapy Response 喂给 ApiIngestSpider.parse，
产出 item 经 FieldMappingPipeline 字段映射后写入真实 SQLite 目标表。
验证完整数据流：HTTP → JSONPath 解析 → 字段映射 → 方言写入 → 统计。

不启动 Scrapy 引擎（避免 reactor 冲突），直接串联 spider + pipeline + writer。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from apps.ingest.models import ConflictStrategy
from apps.ingest.pipelines import FieldMappingPipeline
from apps.ingest.spiders.api_spider import ApiIngestSpider
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


class _StatsCollector:
    """简易 stats 收集器."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value


def _make_response(url: str, body: str) -> TextResponse:
    """构造 Scrapy TextResponse."""
    return TextResponse(url=url, body=body.encode("utf-8"), encoding="utf-8", request=Request(url=url))


def _patch_pipeline_deps(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    """monkeypatch pipeline 的 get_engine 与 DataSource 依赖."""
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


@pytest.fixture(scope="module")
def api_server() -> Iterator[str]:
    """启动本地 HTTP server 返回 JSON 数据，返回 base_url."""
    payload = {
        "data": [
            {"id": 1, "name": "alice", "score": 95},
            {"id": 2, "name": "bob", "score": 87},
            {"id": 3, "name": "carol", "score": 72},
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # type: ignore[missing-override-decorator]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def log_message(self, *_args: Any) -> None:  # type: ignore[missing-override-decorator]
            pass  # 静默日志

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _make_sqlite_engine() -> Engine:
    """创建 SQLite 内存引擎."""
    return create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _fetch_items(api_server: str, spider: ApiIngestSpider) -> list[dict[str, Any]]:
    """从本地 server 获取数据并经 spider.parse 解析为 items."""
    import urllib.request

    with urllib.request.urlopen(f"{api_server}/api") as resp:
        body = resp.read().decode("utf-8")
    response = _make_response(f"{api_server}/api", body)
    return [i for i in spider.parse(response) if isinstance(i, dict)]


@pytest.mark.slow
class TestApiIngestE2E:
    """API 爬取端到端集成测试."""

    def test_full_flow_upsert(self, api_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """完整流程：HTTP → JSONPath 解析 → 字段映射 → UPSERT 写入 → 统计."""
        engine = _make_sqlite_engine()
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, score INTEGER)"))
        _patch_pipeline_deps(monkeypatch, engine)

        mappings = [
            {"source_field": "id", "target_field": "id", "mapping_type": "direct", "fixed_value": "", "is_pk": True},
            {
                "source_field": "name",
                "target_field": "name",
                "mapping_type": "direct",
                "fixed_value": "",
                "is_pk": False,
            },
            {
                "source_field": "score",
                "target_field": "score",
                "mapping_type": "direct",
                "fixed_value": "",
                "is_pk": False,
            },
        ]
        spider = ApiIngestSpider(
            source_url=f"{api_server}/api",
            parse_config={"items_path": "$.data[*]"},
            mappings=mappings,
            target_datasource_id=1,
            target_table="users",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        items = _fetch_items(api_server, spider)
        assert len(items) == 3

        stats = _StatsCollector()
        pipeline = FieldMappingPipeline()
        pipeline._stats = stats
        pipeline.open_spider(spider)
        for item in items:
            pipeline.process_item(item, spider)
        pipeline.close_spider(spider)

        assert stats.values["ingest_rows_written"] == 3
        assert stats.values["ingest_rows_skipped"] == 0

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, score FROM users ORDER BY id")).fetchall()
        assert len(rows) == 3
        assert rows[0] == (1, "alice", 95)
        assert rows[1] == (2, "bob", 87)
        assert rows[2] == (3, "carol", 72)

    def test_upsert_updates_existing(self, api_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """UPSERT 策略下重复执行应更新已有行."""
        engine = _make_sqlite_engine()
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"))
        _patch_pipeline_deps(monkeypatch, engine)

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
        spider = ApiIngestSpider(
            source_url=f"{api_server}/api",
            parse_config={"items_path": "$.data[*]"},
            mappings=mappings,
            target_datasource_id=1,
            target_table="t",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        items = _fetch_items(api_server, spider)

        # 第一次写入
        pipeline = FieldMappingPipeline()
        pipeline._stats = _StatsCollector()
        pipeline.open_spider(spider)
        for item in items:
            pipeline.process_item(item, spider)
        pipeline.close_spider(spider)

        # 第二次写入（相同数据，UPSERT 更新）
        pipeline2 = FieldMappingPipeline()
        pipeline2._stats = _StatsCollector()
        pipeline2.open_spider(spider)
        for item in items:
            pipeline2.process_item(item, spider)
        pipeline2.close_spider(spider)

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM t")).scalar()
        assert count == 3  # 仍是 3 行，不重复

    def test_skip_strategy_skips_conflicts(self, api_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """SKIP 策略下冲突行应跳过."""
        engine = _make_sqlite_engine()
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"))
        _patch_pipeline_deps(monkeypatch, engine)

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
        spider = ApiIngestSpider(
            source_url=f"{api_server}/api",
            parse_config={"items_path": "$.data[*]"},
            mappings=mappings,
            target_datasource_id=1,
            target_table="t",
            conflict_strategy=ConflictStrategy.SKIP,
        )
        items = _fetch_items(api_server, spider)

        # 第一次写入全部
        pipeline = FieldMappingPipeline()
        pipeline._stats = _StatsCollector()
        pipeline.open_spider(spider)
        for item in items:
            pipeline.process_item(item, spider)
        pipeline.close_spider(spider)

        # 第二次写入（全部冲突跳过）
        pipeline2 = FieldMappingPipeline()
        pipeline2._stats = _StatsCollector()
        pipeline2.open_spider(spider)
        for item in items:
            pipeline2.process_item(item, spider)
        pipeline2.close_spider(spider)

        assert pipeline2._stats.values["ingest_rows_written"] == 0
        assert pipeline2._stats.values["ingest_rows_skipped"] == 3
