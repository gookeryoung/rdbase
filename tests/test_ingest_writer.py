"""ingest writer 方言化写入测试.

用 SQLite 真实引擎测试 UPSERT/SKIP/ERROR 策略与无主键场景；
用 mock 连接验证 MySQL/PostgreSQL 方言 SQL 生成。
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.datasources.models import EngineType
from apps.ingest.models import ConflictStrategy
from apps.ingest.writer import _format_table_ref, _quote_ident, write_rows
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


def _make_sqlite_engine() -> Engine:
    """创建 SQLite 内存引擎（StaticPool 保证表跨连接可见）."""
    return create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _create_table(engine: Engine, table: str, pk: bool = True) -> None:
    """创建测试表（id 主键 + name 列）."""
    pk_clause = "PRIMARY KEY" if pk else ""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {table} (id INTEGER {pk_clause}, name TEXT)"))


def _select_all(engine: Engine, table: str) -> list[tuple[Any, ...]]:
    """读取表全部行."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT id, name FROM {table} ORDER BY id"))
        return [tuple(row) for row in result.fetchall()]


# ---------- SQLite UPSERT ----------


class TestSqliteUpsert:
    """SQLite UPSERT 策略测试."""

    def test_insert_new_rows(self) -> None:
        """新行应正常插入."""
        engine = _make_sqlite_engine()
        _create_table(engine, "t")
        rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        written, skipped = write_rows(
            engine,
            rows,
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        assert written == 2
        assert skipped == 0
        assert _select_all(engine, "t") == [(1, "a"), (2, "b")]

    def test_update_on_conflict(self) -> None:
        """主键冲突时应更新已有行."""
        engine = _make_sqlite_engine()
        _create_table(engine, "t")
        # 先插入
        write_rows(
            engine,
            [{"id": 1, "name": "old"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        # 再插入相同主键，应更新
        written, skipped = write_rows(
            engine,
            [{"id": 1, "name": "new"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        assert written == 1
        assert skipped == 0
        assert _select_all(engine, "t") == [(1, "new")]


# ---------- SQLite SKIP ----------


class TestSqliteSkip:
    """SQLite SKIP 策略测试."""

    def test_skip_on_conflict(self) -> None:
        """主键冲突时应跳过，保留原值."""
        engine = _make_sqlite_engine()
        _create_table(engine, "t")
        write_rows(
            engine,
            [{"id": 1, "name": "old"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        written, skipped = write_rows(
            engine,
            [{"id": 1, "name": "new"}, {"id": 2, "name": "b"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.SKIP,
        )
        assert written == 1
        assert skipped == 1
        # id=1 保留 old，id=2 插入 b
        assert _select_all(engine, "t") == [(1, "old"), (2, "b")]


# ---------- SQLite ERROR ----------


class TestSqliteError:
    """SQLite ERROR 策略测试."""

    def test_conflict_raises(self) -> None:
        """主键冲突时应抛出异常."""
        engine = _make_sqlite_engine()
        _create_table(engine, "t")
        write_rows(
            engine,
            [{"id": 1, "name": "a"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        with pytest.raises(ValueError, match="写入目标表失败"):
            write_rows(
                engine,
                [{"id": 1, "name": "b"}],
                target_table="t",
                target_fields=["id", "name"],
                pk_fields=["id"],
                conflict_strategy=ConflictStrategy.ERROR,
            )


# ---------- 无主键 ----------


class TestNoPk:
    """无主键时退化为纯 INSERT."""

    def test_insert_without_pk(self) -> None:
        engine = _make_sqlite_engine()
        _create_table(engine, "t", pk=False)
        rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        written, skipped = write_rows(
            engine,
            rows,
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=[],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        assert written == 2
        assert skipped == 0
        assert len(_select_all(engine, "t")) == 2


# ---------- 空行 ----------


class TestEmptyRows:
    """空行列表应返回 (0, 0)."""

    def test_empty(self) -> None:
        engine = _make_sqlite_engine()
        written, skipped = write_rows(
            engine,
            [],
            target_table="t",
            target_fields=["id"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        assert (written, skipped) == (0, 0)


# ---------- MySQL/PG SQL 生成（mock 连接） ----------


class _FakeCursor:
    """假游标，记录执行的 SQL 与参数，返回固定 rowcount."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.rowcount = 1

    def execute(self, sql: Any, params: dict[str, Any]) -> _FakeCursor:
        self.executed.append((str(sql), params))
        return self


class _FakeConn:
    """假连接，提供 execute 与 dialect 信息."""

    def __init__(self, dialect_name: str) -> None:
        self.dialect_name = dialect_name
        self.cursor = _FakeCursor()

    def execute(self, sql: Any, params: dict[str, Any]) -> _FakeCursor:
        return self.cursor.execute(sql, params)


class _FakeTransaction:
    """假事务上下文."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeConn:
        return self._conn

    def __exit__(self, *_args: Any) -> None:
        return None


class _FakeEngine:
    """假引擎，返回指定方言的假连接."""

    def __init__(self, dialect_name: str) -> None:
        self.dialect = type("Dialect", (), {"name": dialect_name})()
        self._conn = _FakeConn(dialect_name)

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self._conn)

    @property
    def executed(self) -> list[tuple[str, dict[str, Any]]]:
        return self._conn.cursor.executed


class TestMysqlSql:
    """MySQL 方言 SQL 生成测试."""

    def test_upsert_uses_on_duplicate_key(self) -> None:
        engine = _FakeEngine(EngineType.MYSQL)
        write_rows(
            engine,
            [{"id": 1, "name": "a"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        sql = engine.executed[0][0]
        assert "INSERT INTO `t`" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql
        assert "`name`=VALUES(`name`)" in sql

    def test_skip_uses_insert_ignore(self) -> None:
        engine = _FakeEngine(EngineType.MYSQL)
        engine._conn.cursor.rowcount = 0  # 模拟冲突跳过
        write_rows(
            engine,
            [{"id": 1, "name": "a"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.SKIP,
        )
        sql = engine.executed[0][0]
        assert "INSERT IGNORE INTO" in sql


class TestPostgresqlSql:
    """PostgreSQL 方言 SQL 生成测试."""

    def test_upsert_uses_on_conflict_do_update(self) -> None:
        engine = _FakeEngine(EngineType.POSTGRESQL)
        write_rows(
            engine,
            [{"id": 1, "name": "a"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.UPSERT,
        )
        sql = engine.executed[0][0]
        assert 'INSERT INTO "t"' in sql
        assert 'ON CONFLICT ("id") DO UPDATE SET' in sql
        assert '"name"=EXCLUDED."name"' in sql

    def test_skip_uses_on_conflict_do_nothing(self) -> None:
        engine = _FakeEngine(EngineType.POSTGRESQL)
        engine._conn.cursor.rowcount = 0
        write_rows(
            engine,
            [{"id": 1, "name": "a"}],
            target_table="t",
            target_fields=["id", "name"],
            pk_fields=["id"],
            conflict_strategy=ConflictStrategy.SKIP,
        )
        sql = engine.executed[0][0]
        assert 'ON CONFLICT ("id") DO NOTHING' in sql


# ---------- 工具函数 ----------


class TestHelpers:
    """工具函数测试."""

    @pytest.mark.parametrize(
        ("dialect", "expected"),
        [
            (EngineType.MYSQL, "`t`"),
            (EngineType.POSTGRESQL, '"t"'),
            (EngineType.SQLITE, '"t"'),
        ],
        ids=["mysql", "postgresql", "sqlite"],
    )
    def test_format_table_ref(self, dialect: str, expected: str) -> None:
        assert _format_table_ref("t", dialect) == expected

    @pytest.mark.parametrize(
        ("dialect", "expected"),
        [
            (EngineType.MYSQL, "`id`"),
            (EngineType.POSTGRESQL, '"id"'),
            (EngineType.SQLITE, '"id"'),
        ],
        ids=["mysql", "postgresql", "sqlite"],
    )
    def test_quote_ident(self, dialect: str, expected: str) -> None:
        assert _quote_ident("id", dialect) == expected
