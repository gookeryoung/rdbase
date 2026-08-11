"""ingest 端到端集成测试.

启动本地 HTTP server 返回 JSON 数据，构造 Scrapy Response 喂给 ApiIngestSpider.parse，
产出 item 经 FieldMappingPipeline 字段映射后写入真实 SQLite 目标表。
验证完整数据流：HTTP → JSONPath 解析 → 字段映射 → 方言写入 → 统计。

不启动 Scrapy 引擎（避免 reactor 冲突），直接串联 spider + pipeline + writer。

P8-Q5 扩展（iter-55）：

- ``TestDatabaseIngestE2E``：DATABASE 源 SQL → items → 三 pipeline → 写表
- ``TestWebhookReceiveE2E``：HTTP POST /webhook/{token} → pipeline → DB write + 审计
- ``TestIncrementalE2E``：DB_TIMESTAMP 增量策略 last_sync_at 注入 → 仅增量行写入
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from apps.audit.models import AuditAction, AuditLog
from apps.datasources.engine import dispose_all, get_engine
from apps.datasources.models import DataSource, EngineType
from apps.ingest.cleaning import CleaningPipeline
from apps.ingest.models import (
    ConflictStrategy,
    IngestFieldMapping,
    IngestLog,
    IngestLogStatus,
    IngestTask,
    SourceType,
)
from apps.ingest.pipelines import FieldMappingPipeline
from apps.ingest.spiders.api_spider import ApiIngestSpider
from apps.ingest.spiders.database_spider import DatabaseIngestSpider
from apps.ingest.validation import ValidationPipeline
from django.test import Client
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


# ================================================================
# P8-Q5 端到端扩展（iter-55）：DATABASE / Webhook / 增量策略
# ================================================================


class _FullStats:
    """完整 stats 收集器，支持 get_value / set_value / inc_value.

    供 CleaningPipeline / ValidationPipeline / FieldMappingPipeline 共用，
    覆盖 Scrapy stats 接口子集。
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def get_value(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self._values[key] = value

    def inc_value(self, key: str, amount: int = 1) -> None:
        self._values[key] = self._values.get(key, 0) + amount


@pytest.fixture
def _clear_engine_cache_e2e() -> Any:
    """每个测试前后清空 SQLAlchemy 引擎缓存，避免 :memory: 跨测试共享."""
    dispose_all()
    yield
    dispose_all()


def _make_datasource(db: Any, admin_user: Any, name: str) -> DataSource:
    """创建 SQLite 内存数据源 fixture."""
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


def _init_source_users(ds: DataSource) -> None:
    """在源数据源创建 users 表并插入 3 行测试数据."""
    engine = get_engine(ds)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, score INTEGER)"))
        conn.execute(
            text("INSERT INTO users (id, name, score) VALUES (1, 'alice', 95), (2, 'bob', 87), (3, 'carol', 72)")
        )


def _init_source_users_with_ts(ds: DataSource) -> None:
    """在源数据源创建带 updated_at 的 users 表并插入 3 行."""
    engine = get_engine(ds)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, updated_at TEXT)"))
        conn.execute(
            text(
                "INSERT INTO users (id, name, updated_at) VALUES "
                "(1, 'alice', '2026-01-01T00:00:00'), "
                "(2, 'bob', '2026-02-01T00:00:00'), "
                "(3, 'carol', '2026-03-01T00:00:00')"
            )
        )


def _init_target_out(ds: DataSource, cols: str = "id INTEGER PRIMARY KEY, name TEXT, score INTEGER") -> None:
    """在目标数据源创建 out 表."""
    engine = get_engine(ds)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE out ({cols})"))


def _add_mappings(task: IngestTask, *, with_score: bool = True) -> None:
    """为任务添加 id/name[/score] 字段映射."""
    IngestFieldMapping.objects.create(task=task, source_field="id", target_field="id", is_pk=True)
    IngestFieldMapping.objects.create(task=task, source_field="name", target_field="name")
    if with_score:
        IngestFieldMapping.objects.create(task=task, source_field="score", target_field="score")


def _run_pipelines(spider: Any, items: list[dict[str, Any]]) -> _FullStats:
    """串联 Cleaning → Validation → FieldMapping 三 pipeline 处理 items.

    返回共用 stats 收集器，调用方可断言 ingest_rows_written 等统计。
    """
    stats = _FullStats()
    cleaning = CleaningPipeline()
    cleaning._stats = stats
    validation = ValidationPipeline()
    validation._stats = stats
    field_mapping = FieldMappingPipeline()
    field_mapping._stats = stats

    cleaning.open_spider(spider)
    validation.open_spider(spider)
    field_mapping.open_spider(spider)
    try:
        for item in items:
            try:
                cleaned = cleaning.process_item(item, spider)
                validation.process_item(cleaned, spider)
                field_mapping.process_item(cleaned, spider)
            except Exception:  # DropItem 等清洗丢弃不计入写入
                continue
    finally:
        cleaning.close_spider(spider)
        validation.close_spider(spider)
        field_mapping.close_spider(spider)
    return stats


@pytest.mark.slow
@pytest.mark.django_db
class TestDatabaseIngestE2E:
    """DATABASE 源端到端：SQL → items → 三 pipeline → 写表."""

    def test_db_spider_full_flow(
        self,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """完整流程：SQL 查询 → items → 清洗/校验/映射 → UPSERT 写入目标表."""
        source_ds = _make_datasource(db, admin_user, "e2e_db_source")
        target_ds = _make_datasource(db, admin_user, "e2e_db_target")
        _init_source_users(source_ds)
        _init_target_out(target_ds)

        task = IngestTask.objects.create(
            name="e2e_db_task",
            source_type=SourceType.DATABASE,
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name, score FROM users ORDER BY id"},
            target_datasource=target_ds,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        _add_mappings(task)

        spider = DatabaseIngestSpider(
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name, score FROM users ORDER BY id"},
            request_config={},
            incremental_config={},
            task_id=task.pk,
            target_datasource_id=target_ds.pk,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
            mappings=[
                {
                    "source_field": "id",
                    "target_field": "id",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": True,
                },
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
            ],
            clean_config={},
            validation_config={},
            batch_size=500,
        )

        items = list(spider.start())
        assert len(items) == 3

        stats = _run_pipelines(spider, items)
        assert stats.get_value("ingest_rows_written", 0) == 3
        assert stats.get_value("ingest_rows_skipped", 0) == 0

        engine = get_engine(target_ds)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, score FROM out ORDER BY id")).fetchall()
        assert len(rows) == 3
        assert rows[0] == (1, "alice", 95)
        assert rows[1] == (2, "bob", 87)
        assert rows[2] == (3, "carol", 72)

    def test_db_spider_with_cleaning_drop(
        self,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """配置清洗规则丢弃部分行后，仅未丢弃行写入目标表."""
        source_ds = _make_datasource(db, admin_user, "e2e_db_clean_source")
        target_ds = _make_datasource(db, admin_user, "e2e_db_clean_target")
        _init_source_users(source_ds)
        _init_target_out(target_ds)

        task = IngestTask.objects.create(
            name="e2e_db_clean_task",
            source_type=SourceType.DATABASE,
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name, score FROM users ORDER BY id"},
            clean_config={"rules": [{"op": "on_missing", "field": "name", "strategy": "skip"}]},
            target_datasource=target_ds,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        _add_mappings(task)

        spider = DatabaseIngestSpider(
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name, score FROM users ORDER BY id"},
            request_config={},
            incremental_config={},
            task_id=task.pk,
            target_datasource_id=target_ds.pk,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
            mappings=[
                {
                    "source_field": "id",
                    "target_field": "id",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": True,
                },
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
            ],
            clean_config={"rules": [{"op": "on_missing", "field": "name", "strategy": "skip"}]},
            validation_config={},
            batch_size=500,
        )

        items = list(spider.start())
        # 覆盖一条 name 为空触发清洗丢弃
        items[1]["name"] = ""
        stats = _run_pipelines(spider, items)
        assert stats.get_value("ingest_rows_written", 0) == 2
        assert stats.get_value("ingest_rows_skipped", 0) == 0

        engine = get_engine(target_ds)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id FROM out ORDER BY id")).fetchall()
        assert {r[0] for r in rows} == {1, 3}


@pytest.mark.slow
@pytest.mark.django_db
class TestWebhookReceiveE2E:
    """Webhook 被动接收端到端：HTTP POST → pipeline → DB write + 审计."""

    def test_webhook_http_full_flow(
        self,
        client: Client,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """完整流程：POST /webhook/{token} → pipeline → 写表 + IngestLog + 审计."""
        target_ds = _make_datasource(db, admin_user, "e2e_wh_target")
        _init_target_out(target_ds)

        task = IngestTask.objects.create(
            name="e2e_wh_task",
            source_type=SourceType.WEBHOOK,
            source_url="https://example.com/webhook",
            target_datasource=target_ds,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        _add_mappings(task)
        token = task.webhook_token
        assert token is not None

        payload = [
            {"id": 1, "name": "alice", "score": 95},
            {"id": 2, "name": "bob", "score": 87},
        ]
        resp = client.post(
            f"/api/v1/ingest/webhook/{token}",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task.pk
        assert body["rows_read"] == 2
        assert body["rows_written"] == 2
        assert body["rows_skipped"] == 0
        assert body["quality_score"] == 100.0

        # 验证目标表写入
        engine = get_engine(target_ds)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, score FROM out ORDER BY id")).fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, "alice", 95)
        assert rows[1] == (2, "bob", 87)

        # 验证 IngestLog 创建
        log = IngestLog.objects.get(task=task)
        assert log.status == IngestLogStatus.SUCCESS
        assert log.rows_read == 2
        assert log.rows_written == 2
        assert log.quality_score == 100.0

        # 验证审计日志写入
        audit = AuditLog.objects.filter(action=AuditAction.WEBHOOK_RECEIVE).first()
        assert audit is not None
        assert audit.resource_id == str(task.pk)
        assert audit.status == "success"

        # 验证 task.last_sync_at 更新
        task.refresh_from_db()
        assert task.last_sync_at is not None
        assert task.last_run_at is not None

    def test_webhook_dict_payload_wrapped(
        self,
        client: Client,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """dict payload 应包装为单元素列表正常处理."""
        target_ds = _make_datasource(db, admin_user, "e2e_wh_dict_target")
        _init_target_out(target_ds)

        task = IngestTask.objects.create(
            name="e2e_wh_dict_task",
            source_type=SourceType.WEBHOOK,
            source_url="https://example.com/webhook",
            target_datasource=target_ds,
            target_table="out",
        )
        _add_mappings(task)

        resp = client.post(
            f"/api/v1/ingest/webhook/{task.webhook_token}",
            data=json.dumps({"id": 10, "name": "single", "score": 50}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows_read"] == 1
        assert body["rows_written"] == 1

        engine = get_engine(target_ds)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, score FROM out")).fetchall()
        assert rows == [(10, "single", 50)]


@pytest.mark.slow
@pytest.mark.django_db
class TestIncrementalE2E:
    """DB_TIMESTAMP 增量策略端到端：last_sync_at 注入 → 仅增量行写入."""

    def test_db_timestamp_incremental_flow(
        self,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """DB_TIMESTAMP 策略下仅 updated_at > last_sync_at 的行被 yield 并写入."""
        source_ds = _make_datasource(db, admin_user, "e2e_inc_source")
        target_ds = _make_datasource(db, admin_user, "e2e_inc_target")
        _init_source_users_with_ts(source_ds)
        _init_target_out(target_ds, cols="id INTEGER PRIMARY KEY, name TEXT")

        task = IngestTask.objects.create(
            name="e2e_inc_task",
            source_type=SourceType.DATABASE,
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            incremental_config={"strategy": "db_timestamp"},
            target_datasource=target_ds,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        IngestFieldMapping.objects.create(task=task, source_field="id", target_field="id", is_pk=True)
        IngestFieldMapping.objects.create(task=task, source_field="name", target_field="name")

        # 注入 last_sync_at = 2026-01-15，仅 bob(02-01) 与 carol(03-01) 应被拉取
        spider = DatabaseIngestSpider(
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            request_config={"__last_sync_at__": "2026-01-15T00:00:00"},
            incremental_config={"strategy": "db_timestamp"},
            task_id=task.pk,
            target_datasource_id=target_ds.pk,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
            mappings=[
                {
                    "source_field": "id",
                    "target_field": "id",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": True,
                },
                {
                    "source_field": "name",
                    "target_field": "name",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": False,
                },
            ],
            clean_config={},
            validation_config={},
            batch_size=500,
        )

        items = list(spider.start())
        assert len(items) == 2
        assert items[0]["name"] == "bob"
        assert items[1]["name"] == "carol"

        stats = _run_pipelines(spider, items)
        assert stats.get_value("ingest_rows_written", 0) == 2

        engine = get_engine(target_ds)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM out ORDER BY id")).fetchall()
        assert len(rows) == 2
        assert rows[0] == (2, "bob")
        assert rows[1] == (3, "carol")

    def test_db_timestamp_first_run_full_pull(
        self,
        db: Any,
        admin_user: Any,
        _clear_engine_cache_e2e: Any,
    ) -> None:
        """首次执行（无 last_sync_at）应全量拉取所有行."""
        source_ds = _make_datasource(db, admin_user, "e2e_inc_first_source")
        target_ds = _make_datasource(db, admin_user, "e2e_inc_first_target")
        _init_source_users_with_ts(source_ds)
        _init_target_out(target_ds, cols="id INTEGER PRIMARY KEY, name TEXT")

        task = IngestTask.objects.create(
            name="e2e_inc_first_task",
            source_type=SourceType.DATABASE,
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            incremental_config={"strategy": "db_timestamp"},
            target_datasource=target_ds,
            target_table="out",
        )
        IngestFieldMapping.objects.create(task=task, source_field="id", target_field="id", is_pk=True)
        IngestFieldMapping.objects.create(task=task, source_field="name", target_field="name")

        # 首次执行：request_config 无 __last_sync_at__，spider 注入 1970-01-01 全量拉取
        spider = DatabaseIngestSpider(
            source_url=f"datasource://{source_ds.pk}",
            parse_config={"sql": "SELECT id, name FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            request_config={},
            incremental_config={"strategy": "db_timestamp"},
            task_id=task.pk,
            target_datasource_id=target_ds.pk,
            target_table="out",
            conflict_strategy=ConflictStrategy.UPSERT,
            mappings=[
                {
                    "source_field": "id",
                    "target_field": "id",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": True,
                },
                {
                    "source_field": "name",
                    "target_field": "name",
                    "mapping_type": "direct",
                    "fixed_value": "",
                    "is_pk": False,
                },
            ],
            clean_config={},
            validation_config={},
            batch_size=500,
        )

        items = list(spider.start())
        # 首次全量：3 行
        assert len(items) == 3

        stats = _run_pipelines(spider, items)
        assert stats.get_value("ingest_rows_written", 0) == 3

        engine = get_engine(target_ds)
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM out")).scalar()
        assert count == 3
