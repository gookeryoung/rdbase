"""DatabaseIngestSpider 单元测试（iter-54 P8-Q4）.

覆盖：
- ``start`` 直接 yield dict 行（不经 Scrapy 下载器）
- ``_parse_datasource_id`` 解析 ``datasource://{id}`` URL
- ``_build_sql`` / ``_build_params`` 配置读取
- DB_TIMESTAMP 增量策略：注入 ``last_sync_at``、SQL 无占位符时跳过、首次全量
- source_url 格式错误 / 数据源不存在 / 数据源未激活时安全降级
- SQL 查询异常时不抛出（记日志后返回）
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.datasources.engine import dispose_all, get_engine
from apps.datasources.models import DataSource, EngineType
from apps.ingest.spiders.database_spider import DatabaseIngestSpider
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Any:
    """每个测试前后清空 SQLAlchemy 引擎缓存.

    ``:memory:`` SQLite 每个连接独立，跨测试复用缓存引擎会导致表丢失。
    """
    dispose_all()
    yield
    dispose_all()


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 内存数据源 fixture."""
    return DataSource.objects.create(
        name="ds_db_spider",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


def _init_table(ds: DataSource) -> None:
    """在 SQLite 内存库中创建测试表并插入数据."""
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


def _make_spider(
    *,
    source_url: str = "",
    parse_config: dict[str, Any] | None = None,
    request_config: dict[str, Any] | None = None,
    incremental_config: dict[str, Any] | None = None,
    task_id: int = 1,
) -> DatabaseIngestSpider:
    """构造 DatabaseIngestSpider 实例."""
    return DatabaseIngestSpider(
        source_url=source_url,
        parse_config=parse_config or {},
        request_config=request_config or {},
        incremental_config=incremental_config or {},
        task_id=task_id,
    )


class TestParseDatasourceId:
    """``_parse_datasource_id`` URL 解析测试."""

    def test_valid_url(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("datasource://42") == 42

    def test_valid_url_with_path(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("datasource:///100") == 100

    def test_wrong_scheme(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("http://42") is None

    def test_empty_string(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("") is None

    def test_non_numeric_id(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("datasource://abc") is None

    def test_no_path(self) -> None:
        assert DatabaseIngestSpider._parse_datasource_id("datasource://") is None


class TestStartYieldsRows:
    """``start`` 方法直接 yield dict 行测试."""

    def test_yields_all_rows(self, db: Any, datasource: DataSource) -> None:
        """应逐行 yield 全部数据."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id, name FROM users ORDER BY id"},
        )
        rows = list(spider.start())
        assert len(rows) == 3
        assert rows[0] == {"id": 1, "name": "alice"}
        assert rows[2] == {"id": 3, "name": "carol"}

    def test_empty_result_yields_nothing(self, db: Any, datasource: DataSource) -> None:
        """查询结果为空时不 yield."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id FROM users WHERE id > 100"},
        )
        rows = list(spider.start())
        assert rows == []

    def test_parametrized_query(self, db: Any, datasource: DataSource) -> None:
        """参数化查询应正确绑定参数."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={
                "sql": "SELECT id, name FROM users WHERE id > :min_id ORDER BY id",
                "params": {"min_id": 1},
            },
        )
        rows = list(spider.start())
        assert len(rows) == 2
        assert rows[0]["id"] == 2


class TestStartErrorHandling:
    """``start`` 方法错误处理测试."""

    def test_invalid_source_url_yields_nothing(self, db: Any, datasource: DataSource) -> None:
        """source_url 格式错误时不抛异常，yield 空."""
        _init_table(datasource)
        spider = _make_spider(
            source_url="http://wrong",
            parse_config={"sql": "SELECT 1"},
        )
        rows = list(spider.start())
        assert rows == []

    def test_datasource_not_found(self, db: Any, datasource: DataSource) -> None:
        """数据源不存在时不抛异常."""
        spider = _make_spider(
            source_url="datasource://99999",
            parse_config={"sql": "SELECT 1"},
        )
        rows = list(spider.start())
        assert rows == []

    def test_inactive_datasource(self, db: Any, datasource: DataSource) -> None:
        """数据源未激活时不抛异常."""
        _init_table(datasource)
        datasource.is_active = False
        datasource.save(update_fields=["is_active"])
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT 1"},
        )
        rows = list(spider.start())
        assert rows == []

    def test_sql_error_yields_nothing(self, db: Any, datasource: DataSource) -> None:
        """SQL 执行异常时不抛出，记日志后返回空."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT * FROM nonexistent_table"},
        )
        rows = list(spider.start())
        assert rows == []

    def test_missing_sql_config(self, db: Any, datasource: DataSource) -> None:
        """parse_config 无 sql 字段时不抛异常."""
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={},
        )
        rows = list(spider.start())
        assert rows == []

    def test_non_string_sql(self, db: Any, datasource: DataSource) -> None:
        """sql 非字符串时不抛异常."""
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": 123},
        )
        rows = list(spider.start())
        assert rows == []


class TestIncrementalDbTimestamp:
    """DB_TIMESTAMP 增量策略测试."""

    def test_injects_last_sync_when_placeholder_present(self, db: Any, datasource: DataSource) -> None:
        """SQL 含 :last_sync_at 占位符时应注入增量参数."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id, name FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            request_config={"__last_sync_at__": "2026-01-15T00:00:00"},
            incremental_config={"strategy": "db_timestamp"},
        )
        rows = list(spider.start())
        # updated_at > 2026-01-15 的行：bob(02-01) 与 carol(03-01)
        assert len(rows) == 2
        assert rows[0]["name"] == "bob"
        assert rows[1]["name"] == "carol"

    def test_custom_param_name(self, db: Any, datasource: DataSource) -> None:
        """自定义 param_name 时应使用该参数名."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id FROM users WHERE updated_at > :since ORDER BY id"},
            request_config={"__last_sync_at__": "2026-02-15T00:00:00"},
            incremental_config={"strategy": "db_timestamp", "param_name": "since"},
        )
        rows = list(spider.start())
        # updated_at > 2026-02-15：仅 carol(03-01)
        assert len(rows) == 1
        assert rows[0]["id"] == 3

    def test_skips_when_placeholder_missing(self, db: Any, datasource: DataSource) -> None:
        """SQL 不含占位符时应跳过增量过滤，全量拉取."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id FROM users ORDER BY id"},
            request_config={"__last_sync_at__": "2026-01-15T00:00:00"},
            incremental_config={"strategy": "db_timestamp"},
        )
        rows = list(spider.start())
        # 无占位符，跳过增量 -> 全量 3 行
        assert len(rows) == 3

    def test_first_run_full_pull(self, db: Any, datasource: DataSource) -> None:
        """首次执行（last_sync_at 为空）应全量拉取."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id FROM users WHERE updated_at > :last_sync_at ORDER BY id"},
            request_config={},  # 无 __last_sync_at__
            incremental_config={"strategy": "db_timestamp"},
        )
        rows = list(spider.start())
        # 首次全量：占位符无值，SQLAlchemy 不绑定 -> 全部 3 行
        assert len(rows) == 3

    def test_no_incremental_strategy(self, db: Any, datasource: DataSource) -> None:
        """无增量策略时应全量拉取."""
        _init_table(datasource)
        spider = _make_spider(
            source_url=f"datasource://{datasource.pk}",
            parse_config={"sql": "SELECT id FROM users ORDER BY id"},
            request_config={"__last_sync_at__": "2026-01-15T00:00:00"},
            incremental_config={"strategy": "none"},
        )
        rows = list(spider.start())
        assert len(rows) == 3


class TestBuildParams:
    """``_build_params`` 参数构造测试."""

    def test_merges_config_params(self) -> None:
        """应合并 parse_config.params 中的固定参数."""
        spider = _make_spider(
            parse_config={
                "sql": "SELECT 1 WHERE :a > 0",
                "params": {"a": 10, "b": "str"},
            },
        )
        params = spider._build_params()
        assert params == {"a": 10, "b": "str"}

    def test_ignores_non_dict_params(self) -> None:
        """parse_config.params 非 dict 时忽略."""
        spider = _make_spider(
            parse_config={"sql": "SELECT 1", "params": ["bad"]},
        )
        params = spider._build_params()
        assert params == {}

    def test_no_incremental_returns_only_config_params(self) -> None:
        """无增量策略时仅返回 config 参数."""
        spider = _make_spider(
            parse_config={"sql": "SELECT 1", "params": {"x": 1}},
            incremental_config={"strategy": "none"},
        )
        params = spider._build_params()
        assert params == {"x": 1}
