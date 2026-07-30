"""designer 反射层单元测试.

使用 SQLite 内存库 + StaticPool（单连接，确保表跨连接可见）建表后验证反射结果。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from apps.designer.inspector import (
    TableMeta,
    _resolve_schema,
    inspect_table,
    list_databases,
    list_schemas,
    list_tables,
    list_views,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
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
    """在引擎中创建测试表/索引/视图."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100), "
                "age INTEGER DEFAULT 0, "
                "bio TEXT"
                ")"
            )
        )
        # 显式创建单列唯一索引（SQLite 反射会暴露此索引）
        conn.execute(text("CREATE UNIQUE INDEX idx_users_email ON users(email)"))
        conn.execute(
            text(
                "CREATE TABLE posts ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL, "
                "title VARCHAR(200), "
                "CONSTRAINT fk_posts_user FOREIGN KEY (user_id) REFERENCES users(id)"
                ")"
            )
        )
        conn.execute(text("CREATE INDEX idx_posts_user ON posts(user_id)"))
        conn.execute(text("CREATE VIEW active_users AS SELECT id, name FROM users WHERE age > 0"))


def test_list_databases_sqlite_returns_main() -> None:
    """SQLite 应返回 ['main'] 作为数据库列表."""
    engine = _make_memory_engine()
    try:
        assert list_databases(engine) == ["main"]
    finally:
        engine.dispose()


def test_list_schemas_sqlite_returns_main() -> None:
    """SQLite 应返回 ['main'] 作为 Schema 列表."""
    engine = _make_memory_engine()
    try:
        assert list_schemas(engine) == ["main"]
    finally:
        engine.dispose()


def test_list_tables_returns_all_tables() -> None:
    """应返回所有用户表（不含视图）."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        tables = list_tables(engine, schema=None)
        assert set(tables) == {"users", "posts"}
    finally:
        engine.dispose()


def test_list_views_returns_all_views() -> None:
    """应返回所有视图."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        views = list_views(engine, schema=None)
        assert views == ["active_users"]
    finally:
        engine.dispose()


def test_inspect_table_returns_full_metadata() -> None:
    """应返回表完整元数据（字段/主键/外键/索引）."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        meta = inspect_table(engine, "posts", schema=None)
        assert isinstance(meta, TableMeta)
        assert meta.name == "posts"
        assert meta.schema is None
        # 字段顺序与 CREATE TABLE 一致
        col_names = [c.name for c in meta.columns]
        assert col_names == ["id", "user_id", "title"]
        # 主键
        assert meta.primary_key == ("id",)
        # 字段属性
        user_id_col = next(c for c in meta.columns if c.name == "user_id")
        assert user_id_col.nullable is False
        # 外键
        assert len(meta.foreign_keys) == 1
        fk = meta.foreign_keys[0]
        assert fk.name == "fk_posts_user"
        assert fk.columns == ("user_id",)
        assert fk.referred_table == "users"
        assert fk.referred_columns == ("id",)
        # 索引
        idx_names = {i.name for i in meta.indexes}
        assert "idx_posts_user" in idx_names
    finally:
        engine.dispose()


def test_inspect_table_unique_column_flag() -> None:
    """单列 UNIQUE 约束应合并到 column.unique 而非 unique_constraints."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        meta = inspect_table(engine, "users", schema=None)
        email_col = next(c for c in meta.columns if c.name == "email")
        assert email_col.unique is True
        # 单列唯一不在 multi_unique_groups 中
        assert all("email" not in g for g in meta.unique_constraints)
    finally:
        engine.dispose()


def test_inspect_table_defaults_and_types() -> None:
    """应正确读取默认值与字段类型."""
    engine = _make_memory_engine()
    try:
        _setup_tables(engine)
        meta = inspect_table(engine, "users", schema=None)
        age_col = next(c for c in meta.columns if c.name == "age")
        assert age_col.default == "0"
        assert age_col.nullable is True
        name_col = next(c for c in meta.columns if c.name == "name")
        assert name_col.nullable is False
        # SQLite 无表注释
        assert meta.comment is None
    finally:
        engine.dispose()


def test_inspect_table_unknown_raises() -> None:
    """反射不存在的表应抛 SQLAlchemyError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(SQLAlchemyError):
            inspect_table(engine, "nonexistent", schema=None)
    finally:
        engine.dispose()


# ---------- 非 SQLite 方言分支（FakeEngine/FakeInspector） ----------


class _FakeDialect:
    """伪方言，仅暴露 name 属性."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeResult:
    """伪查询结果集."""

    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, ...]]:
        return self._rows


class _FakeConnection:
    """伪连接，execute 返回固定结果集."""

    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._rows = rows

    def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _FakeEngine:
    """伪引擎，用于测试非 SQLite 方言分支."""

    def __init__(self, dialect_name: str, rows: list[tuple[str, ...]] | None = None) -> None:
        self.dialect = _FakeDialect(dialect_name)
        self._rows = rows or []

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._rows)


class _FakeInspector:
    """伪 Inspector，用于测试 inspect_table 的非 SQLite 路径."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        columns: list[dict[str, Any]] | None = None,
        pk: dict[str, Any] | None = None,
        fks: list[dict[str, Any]] | None = None,
        indexes: list[dict[str, Any]] | None = None,
        unique: list[dict[str, Any]] | None = None,
        comment: dict[str, Any] | None = None,
        schemas: list[str] | None = None,
    ) -> None:
        self._columns = columns or []
        self._pk = pk or {}
        self._fks = fks or []
        self._indexes = indexes or []
        self._unique = unique or []
        self._comment = comment or {}
        self._schemas = schemas or []

    def get_schema_names(self) -> list[str]:
        return self._schemas

    def get_columns(self, table_name: str, schema: str | None = None) -> list[dict[str, Any]]:
        return self._columns

    def get_pk_constraint(self, table_name: str, schema: str | None = None) -> dict[str, Any]:
        return self._pk

    def get_foreign_keys(self, table_name: str, schema: str | None = None) -> list[dict[str, Any]]:
        return self._fks

    def get_indexes(self, table_name: str, schema: str | None = None) -> list[dict[str, Any]]:
        return self._indexes

    def get_unique_constraints(self, table_name: str, schema: str | None = None) -> list[dict[str, Any]]:
        return self._unique

    def get_table_comment(self, table_name: str, schema: str | None = None) -> dict[str, Any]:
        return self._comment


def test_list_databases_mysql_uses_show_databases() -> None:
    """MySQL 应通过 SHOW DATABASES 查询数据库列表，空字符串过滤."""
    engine = _FakeEngine("mysql", rows=[("db1",), ("db2",), ("",)])
    assert list_databases(cast(Engine, engine)) == ["db1", "db2"]


def test_list_databases_postgresql_uses_pg_database() -> None:
    """PostgreSQL 应通过 pg_database 查询数据库列表."""
    engine = _FakeEngine("postgresql", rows=[("app",), ("analytics",)])
    assert list_databases(cast(Engine, engine)) == ["app", "analytics"]


def test_list_schemas_non_sqlite_uses_get_schema_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 SQLite 方言应封装 SQLAlchemy get_schema_names."""
    fake_inspector = _FakeInspector(schemas=["public", "info_schema"])

    def _fake_inspect(_engine: Any) -> _FakeInspector:
        return fake_inspector

    monkeypatch.setattr("apps.designer.inspector.inspect", _fake_inspect)
    engine = _FakeEngine("postgresql")
    assert list_schemas(cast(Engine, engine)) == ["public", "info_schema"]


def test_resolve_schema_sqlite_returns_none() -> None:
    """SQLite 应强制返回 None."""
    engine = _FakeEngine("sqlite")
    assert _resolve_schema(cast(Engine, engine), "main") is None


def test_resolve_schema_non_sqlite_returns_input() -> None:
    """非 SQLite 方言应原样返回 schema."""
    engine = _FakeEngine("mysql")
    assert _resolve_schema(cast(Engine, engine), "public") == "public"


def test_inspect_table_with_comment_and_multi_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """应正确读取表注释、单列与多列唯一约束（非 SQLite 路径）.

    覆盖：get_table_comment 正常路径、单列/多列 unique_constraints 收集分支、
    _resolve_schema 非 SQLite 分支。
    """
    fake_inspector = _FakeInspector(
        columns=[
            {"name": "id", "type": "INTEGER", "nullable": False, "autoincrement": True},
            {"name": "a", "type": "INTEGER", "nullable": True},
            {"name": "b", "type": "INTEGER", "nullable": True},
            {"name": "c", "type": "INTEGER", "nullable": True},
        ],
        pk={"constrained_columns": ["id"]},
        unique=[
            {"name": "uk_ab", "column_names": ["a", "b"]},
            {"name": "uk_c", "column_names": ["c"]},
            {"name": "uk_empty", "column_names": []},
        ],
        comment={"text": "测试表注释"},
    )

    def _fake_inspect(_engine: Any) -> _FakeInspector:
        return fake_inspector

    monkeypatch.setattr("apps.designer.inspector.inspect", _fake_inspect)
    engine = _FakeEngine("mysql")
    meta = inspect_table(cast(Engine, engine), "t", schema="public")
    assert meta.comment == "测试表注释"
    assert meta.schema == "public"
    # 多列唯一约束保留在 unique_constraints
    assert meta.unique_constraints == (("a", "b"),)
    # 单列唯一约束合并到 column.unique
    c_col = next(c for c in meta.columns if c.name == "c")
    assert c_col.unique is True
    # 非唯一列
    a_col = next(c for c in meta.columns if c.name == "a")
    assert a_col.unique is False
