"""FieldMappingPipeline 单元测试.

用真实 SQLite 引擎验证字段映射、批量写入与统计收集；
不启动 Scrapy 引擎，直接实例化 pipeline 调用生命周期方法。
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.ingest.models import ConflictStrategy
from apps.ingest.pipelines import FieldMappingPipeline
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


class _FakeStats:
    """假 stats 收集器."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get_value(self, key: str, default: Any = 0) -> Any:
        return self.values.get(key, default)


class _FakeCrawler:
    """假 crawler，提供 stats."""

    def __init__(self) -> None:
        self.stats = _FakeStats()


class _FakeSpider:
    """假 spider，携带 pipeline 所需配置."""

    def __init__(
        self,
        *,
        mappings: list[dict[str, Any]],
        target_datasource_id: int = 1,
        target_table: str = "target",
        conflict_strategy: str = ConflictStrategy.UPSERT,
        batch_size: int = 500,
    ) -> None:
        self.mappings = mappings
        self.target_datasource_id = target_datasource_id
        self.target_table = target_table
        self.conflict_strategy = conflict_strategy
        self.batch_size = batch_size


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
        conn.execute(text("CREATE TABLE target (id INTEGER PRIMARY KEY, name TEXT, type TEXT)"))


@pytest.fixture
def pipeline_with_engine(monkeypatch: pytest.MonkeyPatch) -> tuple[FieldMappingPipeline, Engine, _FakeStats]:
    """构造已初始化的 pipeline + 真实 SQLite 引擎 + stats."""
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

    mappings = [
        {"source_field": "id", "target_field": "id", "mapping_type": "direct", "fixed_value": "", "is_pk": True},
        {"source_field": "name", "target_field": "name", "mapping_type": "direct", "fixed_value": "", "is_pk": False},
        {
            "source_field": "_ignored",
            "target_field": "type",
            "mapping_type": "constant",
            "fixed_value": "api",
            "is_pk": False,
        },
    ]
    spider = _FakeSpider(mappings=mappings)
    stats = _FakeStats()
    pipeline = FieldMappingPipeline()
    pipeline._stats = stats
    pipeline.open_spider(spider)
    return pipeline, engine, stats


class TestOpenSpider:
    """open_spider 初始化测试."""

    def test_no_datasource_skips_init(self) -> None:
        """无 target_datasource_id 时 pipeline 不初始化."""
        pipeline = FieldMappingPipeline()
        spider = _FakeSpider(mappings=[], target_datasource_id=None)
        pipeline.open_spider(spider)
        assert pipeline._engine is None

    def test_no_mappings_skips_init(self) -> None:
        """无字段映射时 pipeline 不初始化."""
        pipeline = FieldMappingPipeline()
        spider = _FakeSpider(mappings=[], target_datasource_id=1)
        pipeline.open_spider(spider)
        assert pipeline._engine is None


class TestProcessItem:
    """字段映射与批量写入测试."""

    def test_direct_mapping(self, pipeline_with_engine: tuple[FieldMappingPipeline, Engine, _FakeStats]) -> None:
        """direct 映射应从源字段读取值写入目标字段."""
        pipeline, engine, _ = pipeline_with_engine
        spider = _FakeSpider(mappings=[])
        pipeline.process_item({"id": 1, "name": "a"}, spider)
        pipeline.close_spider(spider)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, type FROM target")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "a", "api")

    def test_constant_mapping(self, pipeline_with_engine: tuple[FieldMappingPipeline, Engine, _FakeStats]) -> None:
        """constant 映射应使用 fixed_value."""
        pipeline, engine, _ = pipeline_with_engine
        spider = _FakeSpider(mappings=[])
        pipeline.process_item({"id": 1, "name": "x"}, spider)
        pipeline.close_spider(spider)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT type FROM target WHERE id=1")).fetchone()
        assert row[0] == "api"

    def test_batch_flush_on_size(
        self,
        pipeline_with_engine: tuple[FieldMappingPipeline, Engine, _FakeStats],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """batch 满时应自动刷新写入."""
        pipeline, engine, _ = pipeline_with_engine
        # 重新设置 batch_size=2
        pipeline._batch_size = 2
        spider = _FakeSpider(mappings=[])
        for i in range(5):
            pipeline.process_item({"id": i, "name": f"n{i}"}, spider)
        # 前 4 条应已分两批写入（batch=2），第 5 条在 batch 中
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM target")).scalar()
        assert count == 4
        # close 刷剩余
        pipeline.close_spider(spider)
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM target")).scalar()
        assert count == 5

    def test_missing_source_field_becomes_none(
        self, pipeline_with_engine: tuple[FieldMappingPipeline, Engine, _FakeStats]
    ) -> None:
        """源字段缺失时目标字段为 None."""
        pipeline, engine, _ = pipeline_with_engine
        spider = _FakeSpider(mappings=[])
        pipeline.process_item({"id": 1}, spider)  # 无 name
        pipeline.close_spider(spider)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT name FROM target WHERE id=1")).fetchone()
        assert row[0] is None


class TestCloseSpider:
    """close_spider 统计写入测试."""

    def test_stats_written_to_crawler(
        self, pipeline_with_engine: tuple[FieldMappingPipeline, Engine, _FakeStats]
    ) -> None:
        """close_spider 应将 written/skipped 写入 stats."""
        pipeline, _, stats = pipeline_with_engine
        spider = _FakeSpider(mappings=[])
        for i in range(3):
            pipeline.process_item({"id": i, "name": f"n{i}"}, spider)
        pipeline.close_spider(spider)
        assert stats.values["ingest_rows_written"] == 3
        assert stats.values["ingest_rows_skipped"] == 0

    def test_skip_strategy_counts_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SKIP 策略下冲突行应计入 skipped."""
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
        spider = _FakeSpider(mappings=mappings, conflict_strategy=ConflictStrategy.SKIP)
        stats = _FakeStats()
        pipeline = FieldMappingPipeline()
        pipeline._stats = stats
        pipeline.open_spider(spider)

        # 先插入 id=1
        pipeline.process_item({"id": 1, "name": "a"}, spider)
        pipeline.close_spider(spider)

        # 重新打开 pipeline，插入 id=1（冲突跳过）和 id=2（新插入）
        pipeline2 = FieldMappingPipeline()
        pipeline2._stats = _FakeStats()
        pipeline2.open_spider(spider)
        pipeline2.process_item({"id": 1, "name": "b"}, spider)  # 冲突跳过
        pipeline2.process_item({"id": 2, "name": "c"}, spider)  # 新插入
        pipeline2.close_spider(spider)

        assert pipeline2._stats.values["ingest_rows_written"] == 1
        assert pipeline2._stats.values["ingest_rows_skipped"] == 1

    def test_no_engine_writes_zero_stats(self) -> None:
        """未初始化引擎时 close_spider 应写 0 统计（不报错）."""
        pipeline = FieldMappingPipeline()
        pipeline._stats = _FakeStats()
        pipeline.close_spider(_FakeSpider(mappings=[]))
        assert pipeline._stats.values.get("ingest_rows_written") == 0
        assert pipeline._stats.values.get("ingest_rows_skipped") == 0


class TestFromCrawler:
    """from_crawler 工厂测试."""

    def test_binds_stats(self) -> None:
        crawler = _FakeCrawler()
        pipeline = FieldMappingPipeline.from_crawler(crawler)
        assert pipeline._stats is crawler.stats
