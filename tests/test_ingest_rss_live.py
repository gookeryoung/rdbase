"""RSS 爬取真实数据流测试（live，非 mock）.

用真实 RSS mock 文件（tests/fixtures/rss_mock.xml）驱动 RssIngestSpider.parse，
经 FieldMappingPipeline 字段映射与 writer 批量写入 SQLite，验证完整数据流：
RSS XML → feedparser 解析 → 字段归一化 → 字段映射 → UPSERT 写入 → IngestLog 统计。

不启动 Scrapy 引擎（reactor 限制），直接调用 spider.parse + 手动驱动 pipeline，
等价于 Scrapy 引擎在单线程内的执行路径，覆盖真实的数据转换与写入逻辑。

运行方式（-s 显示 print 输出，查看每步实际效果）::

    uv run pytest tests/test_ingest_rss_live.py -v -s
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.engine import dispose_engine
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import (
    IngestFieldMapping,
    IngestLog,
    IngestLogStatus,
    IngestStatus,
    IngestTask,
)
from apps.ingest.pipelines import FieldMappingPipeline
from apps.ingest.spiders.rss_spider import RssIngestSpider
from django.test import Client
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]
from sqlalchemy import text

# RSS mock 文件路径
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rss_mock.xml"


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class _FakeStats:
    """模拟 Scrapy StatsCollector，记录 set_value 调用供断言."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value


@pytest.fixture
def file_datasource(db: Any, admin_user: Any, tmp_path: Path) -> Generator[DataSource, None, None]:
    """文件 SQLite 数据源 fixture（跨连接共享，区别于 :memory:）."""
    db_file = tmp_path / "ingest_live.db"
    ds = DataSource.objects.create(
        name="ds_rss_live",
        engine=EngineType.SQLITE,
        database=str(db_file),
        created_by=admin_user,
    )
    yield ds
    # 清理引擎缓存，避免跨测试污染
    dispose_engine(ds.pk)


def _create_target_table(ds: DataSource) -> None:
    """在目标 SQLite 库预建 rss_articles 表（writer 不自动建表）."""
    from apps.datasources.engine import get_engine

    engine = get_engine(ds)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE rss_articles ("
                "  id TEXT PRIMARY KEY,"
                "  title TEXT,"
                "  url TEXT,"
                "  pub_date TEXT,"
                "  author TEXT,"
                "  summary TEXT"
                ")"
            )
        )


def _make_response(url: str, body: bytes) -> TextResponse:
    """构造 Scrapy TextResponse（模拟 Scrapy 下载器返回的 RSS 响应）."""
    request = Request(url=url)
    return TextResponse(url=url, body=body, request=request)


class TestRssLiveFlow:
    """RSS 爬取真实数据流测试."""

    def test_rss_live_flow(
        self,
        db: Any,
        admin_user: Any,
        file_datasource: DataSource,
    ) -> None:
        """完整 RSS 数据流：XML → 解析 → 映射 → 写入 SQLite → 日志统计.

        步骤：
        1. 创建 IngestTask（RSS 类型，含 feed 元数据合并）+ 6 个字段映射
        2. 预建 SQLite 目标表 rss_articles
        3. 读取 RSS mock 文件，构造 Scrapy Response
        4. 真实 RssIngestSpider.parse 解析 5 条 entry
        5. 真实 FieldMappingPipeline 字段映射 + 批量 UPSERT 写入
        6. 创建 IngestLog 记录统计
        7. 查询 SQLite 验证写入数据
        8. 通过 HTTP API 验证日志与统计可查询
        """
        print("\n" + "=" * 70)
        print("RSS 爬取真实数据流测试（live）")
        print("=" * 70)

        # --- 步骤 1：创建 IngestTask + 字段映射 ---
        task = IngestTask.objects.create(
            name="rss_live_task",
            description="RSS 真实数据流测试任务",
            source_type="rss",
            source_url="https://blog.example.com/feed.xml",
            parse_config={"include_feed_metadata": True},
            request_config={},
            auth_type="none",
            target_datasource=file_datasource,
            target_table="rss_articles",
            conflict_strategy="upsert",
            batch_size=500,
            obey_robots=True,
            scheduler_enabled=False,
            cron_expression="",
            status=IngestStatus.ACTIVE,
            created_by=admin_user,
        )
        # 字段映射：RSS entry 字段 → 目标表字段
        # feedparser 把 RSS <guid> 解析为 entry.id，<description> 为 summary，<pubDate> 为 published_parsed
        mappings_def = [
            ("id", "id", "direct", "", True),
            ("title", "title", "direct", "", False),
            ("link", "url", "direct", "", False),
            ("published_parsed", "pub_date", "direct", "", False),
            ("author", "author", "direct", "", False),
            ("summary", "summary", "direct", "", False),
        ]
        for source_field, target_field, mapping_type, fixed_value, is_pk in mappings_def:
            IngestFieldMapping.objects.create(
                task=task,
                source_field=source_field,
                target_field=target_field,
                mapping_type=mapping_type,
                fixed_value=fixed_value,
                is_pk=is_pk,
            )
        print(f"\n[1] 创建任务: id={task.pk}, name={task.name}")
        print(f"    源: {task.source_url}")
        print(f"    目标: {file_datasource.name}.{task.target_table}")
        print(f"    字段映射: {len(mappings_def)} 个（PK: id）")

        # --- 步骤 2：预建目标表 ---
        _create_target_table(file_datasource)
        print("\n[2] 预建目标表 rss_articles（6 列: id PK, title, url, pub_date, author, summary）")

        # --- 步骤 3：读取 RSS mock 文件并构造 Response ---
        rss_body = FIXTURE_PATH.read_bytes()
        response = _make_response(task.source_url, rss_body)
        print(f"\n[3] 读取 RSS mock: {FIXTURE_PATH.name}（{len(rss_body)} 字节）")

        # --- 步骤 4：真实 spider 解析 ---
        spider = RssIngestSpider.from_task(task)  # type: ignore[assignment]
        items = [item for item in spider.parse(response) if isinstance(item, dict)]
        print(f"\n[4] RssIngestSpider.parse 解析出 {len(items)} 条 entry")
        for idx, item in enumerate(items, 1):
            print(f"    entry[{idx}]: id={item.get('id')}, title={item.get('title')}")
            print(f"              pub_date={item.get('published_parsed')}, author={item.get('author')}")
            print(f"              feed_title={item.get('feed_title')}, tags={item.get('tags')}")
        assert len(items) == 5, f"应解析出 5 条，实际 {len(items)}"

        # --- 步骤 5：真实 pipeline 字段映射 + 写入 ---
        pipeline = FieldMappingPipeline()
        pipeline._stats = _FakeStats()  # type: ignore[assignment]
        pipeline.open_spider(spider)
        for item in items:
            pipeline.process_item(item, spider)
        pipeline.close_spider(spider)

        written = pipeline._stats.values.get("ingest_rows_written", 0)  # type: ignore[union-attr]
        skipped = pipeline._stats.values.get("ingest_rows_skipped", 0)  # type: ignore[union-attr]
        print(f"\n[5] FieldMappingPipeline 写入完成: written={written}, skipped={skipped}")
        assert written == 5, f"应写入 5 行，实际 {written}"
        assert skipped == 0

        # --- 步骤 6：创建 IngestLog ---
        from django.utils import timezone

        log = IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=len(items),
            rows_written=written,
            rows_skipped=skipped,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            duration_ms=42,
        )
        task.last_run_at = timezone.now()
        task.last_sync_at = task.last_run_at
        task.save(update_fields=["last_run_at", "last_sync_at"])
        print(f"\n[6] 创建 IngestLog: id={log.pk}, status={log.status}, rows_read={log.rows_read}")

        # --- 步骤 7：查询 SQLite 验证写入数据 ---
        from apps.datasources.engine import get_engine

        engine = get_engine(file_datasource)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, title, url, pub_date, author, summary FROM rss_articles ORDER BY id")
            ).fetchall()
        print(f"\n[7] 查询 SQLite 目标表 rss_articles：{len(rows)} 行")
        for row in rows:
            print(f"    {row.id} | {row.title} | {row.pub_date} | {row.author}")
            print(f"      url: {row.url}")
            print(f"      summary: {(row.summary or '')[:50]}...")
        assert len(rows) == 5
        # 验证第一条字段正确映射
        first = rows[0]
        assert first.id == "tag:blog.example.com,2024:1"
        assert first.title == "Scrapy 爬虫实战：分页与字段映射"
        assert first.url == "https://blog.example.com/posts/1-scrapy-pagination"
        assert first.author == "alice@example.com (张三)"
        assert first.pub_date and first.pub_date.startswith("2024-07-29")

        # --- 步骤 8：通过 HTTP API 验证日志与统计可查询 ---
        client = Client()
        h = _auth(admin_user)
        logs_resp = client.get(f"/api/v1/ingest/tasks/{task.pk}/logs", **h)
        assert logs_resp.status_code == 200
        logs_body = logs_resp.json()
        print("\n[8] HTTP API 验证:")
        print(f"    GET /tasks/{task.pk}/logs → {len(logs_body)} 条日志")
        print(
            f"      log[0]: status={logs_body[0]['status']}, rows_read={logs_body[0]['rows_read']}, "
            f"rows_written={logs_body[0]['rows_written']}"
        )

        stats_resp = client.get("/api/v1/ingest/stats", **h)
        assert stats_resp.status_code == 200
        stats_body = stats_resp.json()
        print(
            f"    GET /stats → total={stats_body['total']}, succeeded={stats_body['succeeded']}, "
            f"success_rate={stats_body['success_rate']}%"
        )
        print(
            f"              total_rows_read={stats_body['total_rows_read']}, "
            f"total_rows_written={stats_body['total_rows_written']}"
        )
        assert stats_body["total"] == 1
        assert stats_body["succeeded"] == 1
        assert stats_body["success_rate"] == 100.0
        assert stats_body["total_rows_read"] == 5
        assert stats_body["total_rows_written"] == 5

        # --- 验证 UPSERT 幂等：再跑一次写入，行数不变 ---
        print("\n[9] UPSERT 幂等性验证：重新写入同一批数据")
        spider2 = RssIngestSpider.from_task(task)  # type: ignore[assignment]
        pipeline2 = FieldMappingPipeline()
        pipeline2._stats = _FakeStats()  # type: ignore[assignment]
        pipeline2.open_spider(spider2)
        for item in items:
            pipeline2.process_item(item, spider2)
        pipeline2.close_spider(spider2)
        written2 = pipeline2._stats.values.get("ingest_rows_written", 0)  # type: ignore[union-attr]
        with engine.connect() as conn:
            rows2 = conn.execute(text("SELECT COUNT(*) FROM rss_articles")).scalar()
        print(f"    第二次写入: written={written2}, 目标表总行数={rows2}")
        assert written2 == 5  # UPSERT 全部更新（计为 written）
        assert rows2 == 5  # 总行数不变（无新增）

        print("\n" + "=" * 70)
        print("全部验证通过")
        print("=" * 70)
