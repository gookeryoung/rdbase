"""manager 对象管理（视图/存储过程/函数/触发器）模块单元测试.

使用 SQLite 内存库验证视图与触发器的反射/编辑/删除；
MySQL/PG 反射 SQL 用 mock engine 验证分支覆盖。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from apps.manager.objects import (
    ROUTINE_FUNCTION,
    ROUTINE_PROCEDURE,
    ObjectError,
    _build_trigger_drop_sql,
    _clean_definition,
    alter_routine,
    alter_trigger,
    alter_view,
    drop_routine,
    drop_trigger,
    drop_view,
    get_routine_definition,
    get_trigger_definition,
    get_view_definition,
    list_routines,
    list_triggers,
    list_views,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from typing_extensions import override


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


def _setup_view_and_trigger(engine: Engine) -> None:
    """在引擎中创建测试表、视图与触发器."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "age INTEGER DEFAULT 0"
                ")"
            )
        )
        conn.execute(text("INSERT INTO users (name, age) VALUES ('Alice', 30)"))
        # 视图：成年用户
        conn.execute(text("CREATE VIEW adult_view AS SELECT id, name FROM users WHERE age >= 18"))
        # 触发器：插入用户前校验（SQLite 触发器示例）
        conn.execute(
            text(
                "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users "
                "FOR EACH ROW WHEN NEW.age < 0 BEGIN SELECT RAISE(ABORT, 'age 不能为负'); END"
            )
        )


# ============================================================
# _clean_definition
# ============================================================


def test_clean_definition_strips_semicolon_and_whitespace() -> None:
    """应去除首尾空白与末尾分号."""
    result = _clean_definition("  CREATE VIEW v AS SELECT 1;  ", "CREATE VIEW")
    assert result == "CREATE VIEW v AS SELECT 1"


def test_clean_definition_empty_raises() -> None:
    """空定义应抛 ObjectError."""
    with pytest.raises(ObjectError, match="不能为空"):
        _clean_definition("   ", "CREATE VIEW")


def test_clean_definition_wrong_prefix_raises() -> None:
    """前缀不匹配应抛 ObjectError."""
    with pytest.raises(ObjectError, match="须以 CREATE VIEW 开头"):
        _clean_definition("SELECT * FROM t", "CREATE VIEW")


def test_clean_definition_case_insensitive_prefix() -> None:
    """前缀匹配应不区分大小写."""
    result = _clean_definition("create view v as select 1", "CREATE VIEW")
    assert result == "create view v as select 1"


# ============================================================
# 视图（SQLite 真实测试）
# ============================================================


def test_list_views_sqlite_returns_views() -> None:
    """SQLite 应返回已创建的视图名."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        views = list_views(engine, schema=None)
        assert "adult_view" in views
    finally:
        engine.dispose()


def test_list_views_sqlite_excludes_internal() -> None:
    """SQLite 应排除 sqlite_% 内部视图."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        views = list_views(engine, schema=None)
        assert all(not v.startswith("sqlite_") for v in views)
    finally:
        engine.dispose()


def test_get_view_definition_sqlite_returns_sql() -> None:
    """SQLite 应返回视图定义 SQL."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        definition = get_view_definition(engine, "adult_view", schema=None)
        assert "SELECT" in definition.upper()
        assert "users" in definition
    finally:
        engine.dispose()


def test_get_view_definition_nonexistent_raises() -> None:
    """不存在的视图应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="不存在"):
            get_view_definition(engine, "nonexistent_view", schema=None)
    finally:
        engine.dispose()


def test_alter_view_sqlite_replaces_definition() -> None:
    """SQLite 编辑视图应 DROP + CREATE，新定义生效."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        new_def = "CREATE VIEW adult_view AS SELECT id, name, age FROM users WHERE age >= 18"
        alter_view(engine, "adult_view", None, new_def)
        definition = get_view_definition(engine, "adult_view", schema=None)
        assert "age" in definition
    finally:
        engine.dispose()


def test_alter_view_invalid_prefix_raises() -> None:
    """非 CREATE VIEW 语句应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        with pytest.raises(ObjectError, match="须以 CREATE VIEW"):
            alter_view(engine, "adult_view", None, "SELECT * FROM users")
    finally:
        engine.dispose()


def test_drop_view_sqlite_removes_view() -> None:
    """SQLite 删除视图后视图不存在."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        drop_view(engine, "adult_view", None)
        with pytest.raises(ObjectError, match="不存在"):
            get_view_definition(engine, "adult_view", schema=None)
    finally:
        engine.dispose()


# ============================================================
# 存储过程/函数（SQLite 不支持 + mock MySQL/PG）
# ============================================================


def test_list_routines_sqlite_returns_empty() -> None:
    """SQLite 不支持存储过程/函数，应返回空列表."""
    engine = _make_memory_engine()
    try:
        assert list_routines(engine, schema=None) == []
    finally:
        engine.dispose()


def test_get_routine_definition_sqlite_raises() -> None:
    """SQLite 获取存储过程定义应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="SQLite 不支持"):
            get_routine_definition(engine, "f", None, ROUTINE_FUNCTION)
    finally:
        engine.dispose()


def test_alter_routine_sqlite_raises() -> None:
    """SQLite 编辑存储过程应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="SQLite 不支持"):
            alter_routine(engine, "f", None, "CREATE FUNCTION f() RETURNS INT RETURN 1", ROUTINE_FUNCTION)
    finally:
        engine.dispose()


def test_drop_routine_sqlite_raises() -> None:
    """SQLite 删除存储过程应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="SQLite 不支持"):
            drop_routine(engine, "f", None, ROUTINE_FUNCTION)
    finally:
        engine.dispose()


def test_get_routine_definition_invalid_type_raises() -> None:
    """非法 routine_type 应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="routine_type 须为"):
            get_routine_definition(engine, "f", None, "invalid")
    finally:
        engine.dispose()


def test_alter_routine_invalid_type_raises() -> None:
    """非法 routine_type 应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="routine_type 须为"):
            alter_routine(engine, "f", None, "CREATE PROCEDURE p() BEGIN END", "invalid")
    finally:
        engine.dispose()


def test_drop_routine_invalid_type_raises() -> None:
    """非法 routine_type 应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="routine_type 须为"):
            drop_routine(engine, "f", None, "invalid")
    finally:
        engine.dispose()


# ----- MySQL/PG mock 测试 -----


class _MockResult:
    """模拟 SQLAlchemy 结果集."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[_MockRow]:
        return [_MockRow(r) for r in self._rows]

    def fetchone(self) -> _MockRow | None:
        return _MockRow(self._rows[0]) if self._rows else None


class _MockRow:
    """模拟 SQLAlchemy 行（支持索引访问）."""

    def __init__(self, data: tuple[Any, ...]) -> None:
        self._data = data

    def __getitem__(self, idx: int) -> Any:
        return self._data[idx]


class _MockConn:
    """模拟 SQLAlchemy 连接."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[str] = []

    def execute(self, sql: Any, params: Any = None) -> _MockResult:
        sql_str = str(sql)
        self.executed.append(sql_str)
        return _MockResult(self._rows)

    def __enter__(self) -> _MockConn:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _MockBeginConn(_MockConn):
    """模拟事务连接（engine.begin()）."""


class _MockEngine:
    """模拟 SQLAlchemy Engine."""

    def __init__(self, dialect_name: str, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.dialect = MagicMock()
        self.dialect.name = dialect_name
        self._rows = rows or []
        self.connect_rows: list[tuple[Any, ...]] = []
        self.begin_conn: _MockBeginConn | None = None

    def connect(self) -> _MockConn:
        return _MockConn(self._rows)

    def begin(self) -> _MockBeginConn:
        self.begin_conn = _MockBeginConn()
        return self.begin_conn


def test_list_routines_mysql_returns_procedures_and_functions() -> None:
    """MySQL 应返回 PROCEDURE 与 FUNCTION 列表."""
    engine = _MockEngine(
        "mysql",
        rows=[("my_proc", "PROCEDURE"), ("my_func", "FUNCTION")],
    )
    result = list_routines(cast(Engine, engine), schema=None)
    assert len(result) == 2
    assert result[0].name == "my_proc"
    assert result[0].type == ROUTINE_PROCEDURE
    assert result[1].name == "my_func"
    assert result[1].type == ROUTINE_FUNCTION


def test_list_routines_pg_uses_schema_param() -> None:
    """PG 应使用 schema 参数查询 pg_proc."""
    engine = _MockEngine(
        "postgresql",
        rows=[("my_func", "FUNCTION")],
    )
    result = list_routines(cast(Engine, engine), schema="public")
    assert len(result) == 1
    assert result[0].name == "my_func"
    assert result[0].schema == "public"


def test_get_routine_definition_mysql_show_create_procedure() -> None:
    """MySQL 存储过程定义应通过 SHOW CREATE PROCEDURE 获取."""
    engine = _MockEngine("mysql", rows=[("my_proc", "", "CREATE PROCEDURE my_func() BEGIN END")])
    definition = get_routine_definition(cast(Engine, engine), "my_proc", None, ROUTINE_PROCEDURE)
    assert "CREATE PROCEDURE" in definition


def test_get_routine_definition_mysql_show_create_function() -> None:
    """MySQL 函数定义应通过 SHOW CREATE FUNCTION 获取."""
    engine = _MockEngine("mysql", rows=[("my_func", "", "CREATE FUNCTION my_func() RETURNS INT RETURN 1")])
    definition = get_routine_definition(cast(Engine, engine), "my_func", None, ROUTINE_FUNCTION)
    assert "CREATE FUNCTION" in definition


def test_get_routine_definition_mysql_not_found_raises() -> None:
    """MySQL SHOW CREATE 返回空应抛 ObjectError."""
    engine = _MockEngine("mysql", rows=[])
    with pytest.raises(ObjectError, match="不存在"):
        get_routine_definition(cast(Engine, engine), "f", None, ROUTINE_FUNCTION)


def test_get_routine_definition_pg_uses_pg_get_functiondef() -> None:
    """PG 应通过 pg_get_functiondef 获取定义."""
    engine = _MockEngine(
        "postgresql", rows=[("CREATE FUNCTION public.f() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql",)]
    )
    definition = get_routine_definition(cast(Engine, engine), "f", "public", ROUTINE_FUNCTION)
    assert "CREATE FUNCTION" in definition


def test_get_routine_definition_pg_not_found_raises() -> None:
    """PG 函数不存在应抛 ObjectError."""
    engine = _MockEngine("postgresql", rows=[])
    with pytest.raises(ObjectError, match="不存在"):
        get_routine_definition(cast(Engine, engine), "f", "public", ROUTINE_FUNCTION)


def test_alter_routine_mysql_drops_and_creates() -> None:
    """MySQL 编辑存储过程应先 DROP 再 CREATE."""
    engine = _MockEngine("mysql")
    definition = "CREATE PROCEDURE my_proc() BEGIN SELECT 1; END"
    alter_routine(cast(Engine, engine), "my_proc", None, definition, ROUTINE_PROCEDURE)
    assert engine.begin_conn is not None
    executed = engine.begin_conn.executed
    assert any("DROP PROCEDURE IF EXISTS" in s for s in executed)
    assert any("CREATE PROCEDURE" in s for s in executed)


def test_alter_routine_invalid_definition_raises() -> None:
    """非 CREATE PROCEDURE/FUNCTION 语句应抛 ObjectError."""
    engine = _MockEngine("mysql")
    with pytest.raises(ObjectError, match="须以 CREATE PROCEDURE"):
        alter_routine(cast(Engine, engine), "p", None, "SELECT 1", ROUTINE_PROCEDURE)


def test_drop_routine_mysql_drops() -> None:
    """MySQL 删除函数应执行 DROP FUNCTION IF EXISTS."""
    engine = _MockEngine("mysql")
    drop_routine(cast(Engine, engine), "f", None, ROUTINE_FUNCTION)
    assert engine.begin_conn is not None
    assert any("DROP FUNCTION IF EXISTS" in s for s in engine.begin_conn.executed)


# ============================================================
# 触发器（SQLite 真实测试 + mock MySQL/PG）
# ============================================================


def test_list_triggers_sqlite_returns_triggers() -> None:
    """SQLite 应返回已创建的触发器."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        triggers = list_triggers(engine, schema=None)
        assert any(t.name == "trg_before_insert" for t in triggers)
    finally:
        engine.dispose()


def test_list_triggers_sqlite_table_field() -> None:
    """SQLite 触发器应包含关联表名."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        triggers = list_triggers(engine, schema=None)
        trg = next(t for t in triggers if t.name == "trg_before_insert")
        assert trg.table == "users"
    finally:
        engine.dispose()


def test_get_trigger_definition_sqlite_returns_sql() -> None:
    """SQLite 应返回触发器定义 SQL."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        definition = get_trigger_definition(engine, "trg_before_insert", schema=None)
        assert "CREATE TRIGGER" in definition.upper()
        assert "users" in definition
    finally:
        engine.dispose()


def test_get_trigger_definition_nonexistent_raises() -> None:
    """不存在的触发器应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        with pytest.raises(ObjectError, match="不存在"):
            get_trigger_definition(engine, "nonexistent", schema=None)
    finally:
        engine.dispose()


def test_alter_trigger_sqlite_replaces_definition() -> None:
    """SQLite 编辑触发器应 DROP + CREATE."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        new_def = (
            "CREATE TRIGGER trg_before_insert BEFORE INSERT ON users "
            "FOR EACH ROW WHEN NEW.age > 200 BEGIN SELECT RAISE(ABORT, 'age 超出范围'); END"
        )
        alter_trigger(engine, "trg_before_insert", None, new_def, table="users")
        definition = get_trigger_definition(engine, "trg_before_insert", schema=None)
        assert "age 超出范围" in definition
    finally:
        engine.dispose()


def test_alter_trigger_invalid_prefix_raises() -> None:
    """非 CREATE TRIGGER 语句应抛 ObjectError."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        with pytest.raises(ObjectError, match="须以 CREATE TRIGGER"):
            alter_trigger(engine, "trg_before_insert", None, "SELECT 1", table="users")
    finally:
        engine.dispose()


def test_drop_trigger_sqlite_removes_trigger() -> None:
    """SQLite 删除触发器后触发器不存在."""
    engine = _make_memory_engine()
    try:
        _setup_view_and_trigger(engine)
        drop_trigger(engine, "trg_before_insert", None, table="users")
        with pytest.raises(ObjectError, match="不存在"):
            get_trigger_definition(engine, "trg_before_insert", schema=None)
    finally:
        engine.dispose()


# ----- 触发器 mock 测试 -----


def test_list_triggers_mysql_returns_with_event_timing() -> None:
    """MySQL 触发器列表应包含 event/table/timing 字段."""
    engine = _MockEngine(
        "mysql",
        rows=[("trg_insert", "INSERT", "users", "BEFORE")],
    )
    result = list_triggers(cast(Engine, engine), schema=None)
    assert len(result) == 1
    assert result[0].name == "trg_insert"
    assert result[0].event == "INSERT"
    assert result[0].table == "users"
    assert result[0].timing == "BEFORE"


def test_list_triggers_pg_uses_schema_param() -> None:
    """PG 触发器列表应使用 schema 参数."""
    engine = _MockEngine(
        "postgresql",
        rows=[("trg_update", "UPDATE", "users", "AFTER")],
    )
    result = list_triggers(cast(Engine, engine), schema="public")
    assert len(result) == 1
    assert result[0].schema == "public"


def test_get_trigger_definition_mysql_show_create() -> None:
    """MySQL 触发器定义应通过 SHOW CREATE TRIGGER 获取."""
    engine = _MockEngine(
        "mysql", rows=[("trg", "", "CREATE TRIGGER trg BEFORE INSERT ON users FOR EACH ROW BEGIN END")]
    )
    definition = get_trigger_definition(cast(Engine, engine), "trg", schema=None)
    assert "CREATE TRIGGER" in definition


def test_get_trigger_definition_mysql_not_found_raises() -> None:
    """MySQL SHOW CREATE TRIGGER 返回空应抛 ObjectError."""
    engine = _MockEngine("mysql", rows=[])
    with pytest.raises(ObjectError, match="不存在"):
        get_trigger_definition(cast(Engine, engine), "trg", schema=None)


def test_get_trigger_definition_pg_uses_pg_get_triggerdef() -> None:
    """PG 触发器定义应通过 pg_get_triggerdef 获取."""
    engine = _MockEngine(
        "postgresql", rows=[("CREATE TRIGGER trg BEFORE INSERT ON users FOR EACH ROW EXECUTE FUNCTION f()",)]
    )
    definition = get_trigger_definition(cast(Engine, engine), "trg", schema="public")
    assert "CREATE TRIGGER" in definition


# ============================================================
# _build_trigger_drop_sql
# ============================================================


def test_build_trigger_drop_sql_mysql() -> None:
    """MySQL DROP TRIGGER 应包含 schema.name 引用."""
    sql = _build_trigger_drop_sql("mysql", "trg", "mydb", "users")
    assert sql == "DROP TRIGGER IF EXISTS `mydb`.`trg`"


def test_build_trigger_drop_sql_pg_requires_table() -> None:
    """PG DROP TRIGGER 缺少 table 应抛 ObjectError."""
    with pytest.raises(ObjectError, match="需要关联表名"):
        _build_trigger_drop_sql("postgresql", "trg", "public", None)


def test_build_trigger_drop_sql_pg_with_table() -> None:
    """PG DROP TRIGGER 应包含 ON table."""
    sql = _build_trigger_drop_sql("postgresql", "trg", "public", "users")
    assert sql == 'DROP TRIGGER IF EXISTS trg ON "public"."users"'


def test_build_trigger_drop_sql_sqlite() -> None:
    """SQLite DROP TRIGGER 应使用双引号引用触发器名."""
    sql = _build_trigger_drop_sql("sqlite", "trg", None, None)
    assert sql == 'DROP TRIGGER IF EXISTS "trg"'


def test_build_trigger_drop_sql_pg_no_if_exists() -> None:
    """PG DROP TRIGGER 不带 IF EXISTS."""
    sql = _build_trigger_drop_sql("postgresql", "trg", "public", "users", if_exists=False)
    assert "IF EXISTS" not in sql


def test_alter_trigger_pg_drops_with_table() -> None:
    """PG 编辑触发器应使用 table 参数构造 DROP."""
    engine = _MockEngine("postgresql")
    definition = "CREATE TRIGGER trg BEFORE INSERT ON users FOR EACH ROW BEGIN END"
    alter_trigger(cast(Engine, engine), "trg", "public", definition, table="users")
    assert engine.begin_conn is not None
    executed = engine.begin_conn.executed
    assert any("DROP TRIGGER IF EXISTS trg ON" in s for s in executed)
    assert any("CREATE TRIGGER" in s for s in executed)


def test_drop_trigger_pg_requires_table() -> None:
    """PG 删除触发器缺少 table 应抛 ObjectError."""
    engine = _MockEngine("postgresql")
    with pytest.raises(ObjectError, match="需要关联表名"):
        drop_trigger(cast(Engine, engine), "trg", "public", table=None)


# ============================================================
# PostgreSQL 视图分支补充（覆盖 objects.py:122, 148）
# ============================================================


class _MockPgConnWithParams(_MockConn):
    """Mock 连接同时记录执行时的 SQL 与参数."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        super().__init__(rows)
        self.last_params: Any = None

    @override
    def execute(self, sql: Any, params: Any = None) -> _MockResult:
        self.last_params = params
        return super().execute(sql, params)


class _MockPgEngine(_MockEngine):
    """PostgreSQL Mock Engine，支持记录 connect 时的参数."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        super().__init__("postgresql", rows)
        self.last_conn: _MockPgConnWithParams | None = None

    @override
    def connect(self) -> _MockPgConnWithParams:
        self.last_conn = _MockPgConnWithParams(self._rows)
        return self.last_conn


def test_list_views_pg_passes_schema_param() -> None:
    """PostgreSQL list_views 应将 schema 参数传递给 SQL."""
    engine = _MockPgEngine(rows=[("my_view",), ("other_view",)])
    result = list_views(cast(Engine, engine), schema="custom_schema")
    assert len(result) == 2
    assert result[0] == "my_view"
    assert engine.last_conn is not None
    assert engine.last_conn.last_params == {"schema": "custom_schema"}


def test_list_views_pg_defaults_to_public() -> None:
    """PostgreSQL list_views schema=None 时应默认使用 'public'."""
    engine = _MockPgEngine(rows=[])
    list_views(cast(Engine, engine), schema=None)
    assert engine.last_conn is not None
    assert engine.last_conn.last_params == {"schema": "public"}


def test_list_views_pg_schema_empty_string_defaults_to_public() -> None:
    """PostgreSQL list_views schema='' 时应默认使用 'public'."""
    engine = _MockPgEngine(rows=[])
    list_views(cast(Engine, engine), schema="")
    assert engine.last_conn is not None
    assert engine.last_conn.last_params == {"schema": "public"}


def test_get_view_definition_pg_passes_schema_param() -> None:
    """PostgreSQL get_view_definition 应将 schema 参数传递给 SQL."""
    engine = _MockPgEngine(rows=[("SELECT 1",)])
    result = get_view_definition(cast(Engine, engine), "my_view", schema="custom_schema")
    assert result == "SELECT 1"
    assert engine.last_conn is not None
    assert engine.last_conn.last_params == {"name": "my_view", "schema": "custom_schema"}


def test_get_view_definition_pg_defaults_to_public() -> None:
    """PostgreSQL get_view_definition schema=None 时应默认使用 'public'."""
    engine = _MockPgEngine(rows=[("SELECT 1",)])
    get_view_definition(cast(Engine, engine), "v", schema=None)
    assert engine.last_conn is not None
    assert engine.last_conn.last_params == {"name": "v", "schema": "public"}


def test_get_view_definition_pg_view_not_found_raises() -> None:
    """PostgreSQL 视图不存在应抛 ObjectError."""
    engine = _MockPgEngine(rows=[])
    with pytest.raises(ObjectError, match="不存在"):
        get_view_definition(cast(Engine, engine), "nonexistent", schema="public")
