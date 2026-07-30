"""manager 数据查询模块单元测试.

使用 SQLite 内存库 + StaticPool（单连接，确保表跨连接可见）建表后验证查询结果。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from apps.manager.query import (
    QueryError,
    _build_where_clause,
    _format_table_ref,
    _quote_ident,
    _resolve_schema,
    count_table_rows,
    get_column_names,
    query_table_rows,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


def _make_memory_engine() -> Engine:
    """构造 SQLite 内存库引擎（StaticPool 单连接，确保建表后跨连接可见）."""
    return cast(
        Engine,
        create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            future=True,
        ),
    )


def _setup_tables(engine: Engine) -> None:
    """在引擎中创建测试表并插入数据."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100), "
                "age INTEGER DEFAULT 0"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, name, email, age) VALUES "
                "(1, 'Alice', 'alice@example.com', 30), "
                "(2, 'Bob', 'bob@example.com', 25), "
                "(3, 'Charlie', 'charlie@example.com', 35), "
                "(4, 'David', 'david@example.com', 28), "
                "(5, 'Eve', 'eve@example.com', 22)"
            )
        )


# ---------- 标识符引用 ----------


def test_quote_ident_mysql_uses_backticks() -> None:
    """MySQL 方言应使用反引号引用标识符."""
    assert _quote_ident("name", "mysql") == "`name`"


def test_quote_ident_non_mysql_uses_double_quotes() -> None:
    """非 MySQL 方言应使用双引号引用标识符."""
    assert _quote_ident("name", "postgresql") == '"name"'
    assert _quote_ident("name", "sqlite") == '"name"'


def test_format_table_ref_with_schema() -> None:
    """非 SQLite 方言应包含 schema 前缀."""
    assert _format_table_ref("users", "public", "postgresql") == '"public"."users"'
    assert _format_table_ref("users", "public", "mysql") == "`public`.`users`"


def test_format_table_ref_sqlite_ignores_schema() -> None:
    """SQLite 方言应忽略 schema."""
    assert _format_table_ref("users", "main", "sqlite") == '"users"'
    assert _format_table_ref("users", None, "sqlite") == '"users"'


# ---------- _resolve_schema ----------


def test_resolve_schema_sqlite_returns_none() -> None:
    """SQLite 应强制返回 None."""
    engine = _make_memory_engine()
    try:
        assert _resolve_schema(engine, "main") is None
    finally:
        engine.dispose()


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEngine:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _FakeDialect(dialect_name)

    def connect(self) -> Any:  # pragma: no cover - 未在 resolve_schema 中调用
        raise AssertionError("不应调用 connect")


def test_resolve_schema_non_sqlite_returns_input() -> None:
    """非 SQLite 方言应原样返回 schema（用 FakeEngine 覆盖分支）."""
    engine = _FakeEngine("mysql")
    assert _resolve_schema(cast(Engine, engine), "public") == "public"


# ---------- get_column_names ----------


def test_get_column_names_returns_all() -> None:
    """应返回表的所有列名."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        cols = get_column_names(engine, "users", schema=None)
        assert cols == ["id", "name", "email", "age"]
    finally:
        engine.dispose()


def test_get_column_names_unknown_table_raises() -> None:
    """不存在的表应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(QueryError):
            get_column_names(engine, "nonexistent", schema=None)
    finally:
        engine.dispose()


# ---------- _build_where_clause ----------


def test_build_where_clause_empty_filters() -> None:
    """空 filters 应返回空字符串与空参数."""
    sql, params = _build_where_clause({}, "sqlite")
    assert sql == ""
    assert params == {}


def test_build_where_clause_eq() -> None:
    """eq 操作符应生成 = 占位符."""
    sql, params = _build_where_clause({"name": {"op": "eq", "val": "Alice"}}, "sqlite")
    assert sql == '"name" = :f0'
    assert params == {"f0": "Alice"}


def test_build_where_clause_in() -> None:
    """in 操作符应展开多个占位符."""
    sql, params = _build_where_clause({"id": {"op": "in", "val": [1, 2, 3]}}, "sqlite")
    assert sql == '"id" IN (:f0_0, :f0_1, :f0_2)'
    assert params == {"f0_0": 1, "f0_1": 2, "f0_2": 3}


def test_build_where_clause_in_empty_list_raises() -> None:
    """in 操作符空列表应抛 QueryError."""
    with pytest.raises(QueryError):
        _build_where_clause({"id": {"op": "in", "val": []}}, "sqlite")


def test_build_where_clause_multiple_filters() -> None:
    """多个筛选条件应用 AND 连接."""
    sql, params = _build_where_clause(
        {"name": {"op": "like", "val": "A%"}, "age": {"op": "gt", "val": 18}},
        "sqlite",
    )
    assert sql == '"name" LIKE :f0 AND "age" > :f1'
    assert params == {"f0": "A%", "f1": 18}


def test_build_where_clause_all_comparators() -> None:
    """应支持全部比较操作符."""
    ops = ["eq", "ne", "gt", "lt", "ge", "le", "like"]
    expected_symbols = ["=", "<>", ">", "<", ">=", "<=", "LIKE"]
    for op, symbol in zip(ops, expected_symbols, strict=True):
        sql, _ = _build_where_clause({"col": {"op": op, "val": 1}}, "sqlite")
        assert sql == f'"col" {symbol} :f0'


# ---------- query_table_rows ----------


def test_query_table_rows_default() -> None:
    """默认应返回所有行、所有列."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(engine, "users", schema=None)
        assert total == 5
        assert len(rows) == 5
        assert set(rows[0].keys()) == {"id", "name", "email", "age"}
    finally:
        engine.dispose()


def test_query_table_rows_pagination() -> None:
    """分页应正确返回对应页的行."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(engine, "users", schema=None, page=2, page_size=2)
        assert total == 5
        assert len(rows) == 2
        # 第二页应返回 id=3, 4（按插入顺序）
        assert [r["id"] for r in rows] == [3, 4]
    finally:
        engine.dispose()


def test_query_table_rows_pagination_beyond_end() -> None:
    """分页超出末尾应返回空列表但 total 不变."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(engine, "users", schema=None, page=10, page_size=2)
        assert total == 5
        assert rows == []
    finally:
        engine.dispose()


def test_query_table_rows_order_by_asc() -> None:
    """应支持升序排序."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, _ = query_table_rows(
            engine,
            "users",
            schema=None,
            order_by="age",
            order_dir="asc",
        )
        ages = [r["age"] for r in rows]
        assert ages == sorted(ages)
        assert ages[0] == 22  # Eve 最年轻
    finally:
        engine.dispose()


def test_query_table_rows_order_by_desc() -> None:
    """应支持降序排序."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, _ = query_table_rows(
            engine,
            "users",
            schema=None,
            order_by="age",
            order_dir="desc",
        )
        ages = [r["age"] for r in rows]
        assert ages == sorted(ages, reverse=True)
        assert ages[0] == 35  # Charlie 最年长
    finally:
        engine.dispose()


def test_query_table_rows_columns_subset() -> None:
    """应支持显式指定返回的列."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, _ = query_table_rows(
            engine,
            "users",
            schema=None,
            columns=["id", "name"],
        )
        assert len(rows) == 5
        assert set(rows[0].keys()) == {"id", "name"}
    finally:
        engine.dispose()


def test_query_table_rows_filter_eq() -> None:
    """应支持 eq 筛选."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(
            engine,
            "users",
            schema=None,
            filters={"name": {"op": "eq", "val": "Alice"}},
        )
        assert total == 1
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
    finally:
        engine.dispose()


def test_query_table_rows_filter_like() -> None:
    """应支持 like 模糊筛选."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(
            engine,
            "users",
            schema=None,
            filters={"email": {"op": "like", "val": "%@example.com"}},
        )
        assert total == 5
        assert len(rows) == 5
    finally:
        engine.dispose()


def test_query_table_rows_filter_in() -> None:
    """应支持 in 筛选."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(
            engine,
            "users",
            schema=None,
            filters={"id": {"op": "in", "val": [1, 3, 5]}},
        )
        assert total == 3
        assert {r["id"] for r in rows} == {1, 3, 5}
    finally:
        engine.dispose()


def test_query_table_rows_filter_combined() -> None:
    """应支持多列组合筛选."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(
            engine,
            "users",
            schema=None,
            filters={
                "age": {"op": "gt", "val": 25},
                "name": {"op": "like", "val": "A%"},
            },
        )
        assert total == 1
        assert rows[0]["name"] == "Alice"
    finally:
        engine.dispose()


def test_query_table_rows_invalid_column_raises() -> None:
    """非法列名应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            query_table_rows(engine, "users", schema=None, columns=["nonexistent"])
    finally:
        engine.dispose()


def test_query_table_rows_invalid_order_by_raises() -> None:
    """非法排序字段应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            query_table_rows(engine, "users", schema=None, order_by="nonexistent")
    finally:
        engine.dispose()


def test_query_table_rows_invalid_filter_column_raises() -> None:
    """非法筛选列名应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            query_table_rows(
                engine,
                "users",
                schema=None,
                filters={"nonexistent": {"op": "eq", "val": 1}},
            )
    finally:
        engine.dispose()


def test_query_table_rows_invalid_op_raises() -> None:
    """非法操作符应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            query_table_rows(
                engine,
                "users",
                schema=None,
                filters={"name": {"op": "contains", "val": "A"}},
            )
    finally:
        engine.dispose()


def test_query_table_rows_invalid_page_raises() -> None:
    """page < 1 应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(QueryError):
            query_table_rows(engine, "users", schema=None, page=0)
    finally:
        engine.dispose()


def test_query_table_rows_invalid_page_size_raises() -> None:
    """page_size < 1 应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(QueryError):
            query_table_rows(engine, "users", schema=None, page_size=0)
    finally:
        engine.dispose()


def test_query_table_rows_invalid_order_dir_raises() -> None:
    """非法 order_dir 应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            query_table_rows(engine, "users", schema=None, order_dir="random")
    finally:
        engine.dispose()


def test_query_table_rows_unknown_table_raises() -> None:
    """不存在的表应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(QueryError):
            query_table_rows(engine, "nonexistent", schema=None)
    finally:
        engine.dispose()


def test_query_table_rows_schema_ignored_for_sqlite() -> None:
    """SQLite 应忽略 schema 参数."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        rows, total = query_table_rows(engine, "users", schema="main")
        assert total == 5
        assert len(rows) == 5
    finally:
        engine.dispose()


# ---------- count_table_rows ----------


def test_count_table_rows_default() -> None:
    """应统计全表行数."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        assert count_table_rows(engine, "users", schema=None) == 5
    finally:
        engine.dispose()


def test_count_table_rows_with_filter() -> None:
    """应统计满足筛选的行数."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        assert (
            count_table_rows(
                engine,
                "users",
                schema=None,
                filters={"age": {"op": "gt", "val": 25}},
            )
            == 3
        )
    finally:
        engine.dispose()


def test_count_table_rows_unknown_table_raises() -> None:
    """不存在的表应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(QueryError):
            count_table_rows(engine, "nonexistent", schema=None)
    finally:
        engine.dispose()


def test_count_table_rows_invalid_filter_column_raises() -> None:
    """非法筛选列名应抛 QueryError."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        with pytest.raises(QueryError):
            count_table_rows(
                engine,
                "users",
                schema=None,
                filters={"nonexistent": {"op": "eq", "val": 1}},
            )
    finally:
        engine.dispose()
