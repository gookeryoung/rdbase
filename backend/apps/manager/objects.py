"""数据库对象（视图/存储过程/函数/触发器）反射与编辑模块.

提供视图、存储过程、函数、触发器的元数据读取与定义编辑能力。
基于 SQLAlchemy ``text()`` 直接查询各方言系统表，屏蔽 MySQL/PostgreSQL/SQLite 差异。

设计要点：
- ``list_views``/``get_view_definition``: 视图列表与定义 SQL
- ``list_routines``/``get_routine_definition``: 存储过程/函数列表与定义（MySQL/PG；SQLite 不支持）
- ``list_triggers``/``get_trigger_definition``: 触发器列表与定义
- ``alter_view``/``alter_routine``/``alter_trigger``: 编辑对象（DROP IF EXISTS + CREATE，单事务）
- ``drop_view``/``drop_routine``/``drop_trigger``: 删除对象
- 标识符引用复用 ``query._quote_ident``/``_format_table_ref``，避免重复实现
- SQLite 仅支持视图与触发器，存储过程/函数相关接口返回空列表或抛 ``ObjectError``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.datasources.models import EngineType

from .query import _format_table_ref, _quote_ident, _resolve_schema


class ObjectError(ValueError):
    """对象操作错误（如对象不存在、方言不支持、定义 SQL 非法等）."""


# 对象类型枚举（与前端 RoutineType 对应）
ROUTINE_PROCEDURE = "procedure"
ROUTINE_FUNCTION = "function"
_ROUTINE_TYPES: frozenset[str] = frozenset({ROUTINE_PROCEDURE, ROUTINE_FUNCTION})


# ============================================================
# 数据类
# ============================================================


@dataclass(frozen=True)
class ViewMeta:
    """视图元数据."""

    name: str
    schema: str | None
    definition: str


@dataclass(frozen=True)
class RoutineMeta:
    """存储过程/函数元数据."""

    name: str
    schema: str | None
    type: str  # "procedure" | "function"
    definition: str


@dataclass(frozen=True)
class TriggerMeta:
    """触发器元数据."""

    name: str
    schema: str | None
    event: str  # INSERT/UPDATE/DELETE
    table: str  # 触发器关联的表
    timing: str  # BEFORE/AFTER/INSTEAD OF
    definition: str


# ============================================================
# 视图
# ============================================================


# 各方言查询视图列表的 SQL
# MySQL/PG 用 information_schema.views；SQLite 用 sqlite_master
_VIEW_LIST_SQL: dict[str, str] = {
    EngineType.MYSQL: (
        "SELECT table_name FROM information_schema.views WHERE table_schema = DATABASE() ORDER BY table_name"
    ),
    EngineType.POSTGRESQL: (
        "SELECT table_name FROM information_schema.views WHERE table_schema = :schema ORDER BY table_name"
    ),
    EngineType.SQLITE: (
        "SELECT name FROM sqlite_master WHERE type = 'view' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ),
}

# 各方言查询视图定义的 SQL
_VIEW_DEF_SQL: dict[str, str] = {
    EngineType.MYSQL: (
        "SELECT view_definition FROM information_schema.views WHERE table_schema = DATABASE() AND table_name = :name"
    ),
    EngineType.POSTGRESQL: (
        "SELECT view_definition FROM information_schema.views WHERE table_schema = :schema AND table_name = :name"
    ),
    EngineType.SQLITE: ("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = :name"),
}


def list_views(engine: Engine, schema: str | None = None) -> list[str]:
    """列出指定 Schema 下的所有视图名.

    Args:
        engine: SQLAlchemy 引擎。
        schema: Schema 名（SQLite 强制 None；MySQL 用当前数据库；PG 用指定 schema）。

    Returns:
        视图名列表（按字母序）。
    """
    dialect = engine.dialect.name
    sql = _VIEW_LIST_SQL.get(dialect)
    if sql is None:  # pragma: no cover - 当前仅支持三种方言
        raise ObjectError(f"方言 {dialect} 暂不支持视图反射")
    params: dict[str, Any] = {}
    if dialect == EngineType.POSTGRESQL:
        params["schema"] = schema or "public"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [cast(str, row[0]) for row in rows if row[0]]


def get_view_definition(engine: Engine, name: str, schema: str | None = None) -> str:
    """获取视图定义 SQL.

    Args:
        engine: SQLAlchemy 引擎。
        name: 视图名。
        schema: Schema 名。

    Returns:
        视图定义 SQL 字符串（CREATE VIEW ... 形式或 SELECT ... 形式）。

    Raises:
        ObjectError: 视图不存在或方言不支持。
    """
    dialect = engine.dialect.name
    sql = _VIEW_DEF_SQL.get(dialect)
    if sql is None:  # pragma: no cover
        raise ObjectError(f"方言 {dialect} 暂不支持视图反射")
    params: dict[str, Any] = {"name": name}
    if dialect == EngineType.POSTGRESQL:
        params["schema"] = schema or "public"
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).fetchone()
    if row is None or row[0] is None:
        raise ObjectError(f"视图 {name} 不存在")
    return cast(str, row[0])


def alter_view(engine: Engine, name: str, schema: str | None, definition: str) -> None:
    """编辑视图：DROP IF EXISTS + CREATE（单事务）.

    Args:
        engine: SQLAlchemy 引擎。
        name: 视图名。
        schema: Schema 名。
        definition: 完整的 CREATE VIEW 语句。

    Raises:
        ObjectError: definition 为空或非 CREATE VIEW 语句。
        SQLAlchemyError: 底层执行失败（事务回滚）。
    """
    cleaned = _clean_definition(definition, "CREATE VIEW")
    dialect = engine.dialect.name
    effective_schema = _resolve_schema(engine, schema)
    view_ref = _format_table_ref(name, effective_schema, dialect)
    drop_sql = f"DROP VIEW IF EXISTS {view_ref}"
    with engine.begin() as conn:
        conn.execute(text(drop_sql))
        conn.execute(text(cleaned))


def drop_view(engine: Engine, name: str, schema: str | None) -> None:
    """删除视图.

    Raises:
        SQLAlchemyError: 底层执行失败。
    """
    dialect = engine.dialect.name
    effective_schema = _resolve_schema(engine, schema)
    view_ref = _format_table_ref(name, effective_schema, dialect)
    with engine.begin() as conn:
        conn.execute(text(f"DROP VIEW IF EXISTS {view_ref}"))


# ============================================================
# 存储过程/函数
# ============================================================


# MySQL: information_schema.routines 区分 PROCEDURE/FUNCTION
# PG: pg_proc 关联 pg_namespace，统一返回函数（PG 11+ 才有 PROCEDURE，pg_proc 也包含）
_ROUTINE_LIST_SQL: dict[str, str] = {
    EngineType.MYSQL: (
        "SELECT routine_name, routine_type FROM information_schema.routines "
        "WHERE routine_schema = DATABASE() ORDER BY routine_name"
    ),
    EngineType.POSTGRESQL: (
        "SELECT p.proname, "
        "CASE WHEN p.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS routine_type "
        "FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = :schema ORDER BY p.proname"
    ),
}


def list_routines(engine: Engine, schema: str | None = None) -> list[RoutineMeta]:
    """列出存储过程与函数.

    SQLite 不支持存储过程/函数，返回空列表。
    MySQL/PG 返回 ``RoutineMeta`` 列表（不含定义，定义需单独调用 :func:`get_routine_definition`）。

    Args:
        engine: SQLAlchemy 引擎。
        schema: Schema 名。

    Returns:
        ``RoutineMeta`` 列表（definition 字段为空字符串，需单独获取）。
    """
    dialect = engine.dialect.name
    if dialect == EngineType.SQLITE:
        return []
    sql = _ROUTINE_LIST_SQL.get(dialect)
    if sql is None:  # pragma: no cover
        raise ObjectError(f"方言 {dialect} 暂不支持存储过程/函数反射")
    params: dict[str, Any] = {}
    if dialect == EngineType.POSTGRESQL:
        params["schema"] = schema or "public"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    result: list[RoutineMeta] = []
    for row in rows:
        rname = cast(str, row[0])
        rtype_raw = cast(str, row[1]).lower()
        rtype = ROUTINE_PROCEDURE if rtype_raw == "procedure" else ROUTINE_FUNCTION
        result.append(RoutineMeta(name=rname, schema=schema, type=rtype, definition=""))
    return result


# 各方言查询单个 routine 定义的 SQL
# MySQL: SHOW CREATE PROCEDURE/FUNCTION 返回多列，取 Create Procedure/Function 列
# PG: pg_get_functiondef 返回完整定义文本
_ROUTINE_DEF_SQL: dict[str, str] = {
    EngineType.MYSQL: "",  # 用 SHOW CREATE，单独处理
    EngineType.POSTGRESQL: (
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = :schema AND p.proname = :name"
    ),
}


def get_routine_definition(
    engine: Engine,
    name: str,
    schema: str | None,
    routine_type: str,
) -> str:
    """获取存储过程/函数定义 SQL.

    Args:
        engine: SQLAlchemy 引擎。
        name: 对象名。
        schema: Schema 名。
        routine_type: ``procedure`` 或 ``function``。

    Returns:
        定义 SQL 字符串。

    Raises:
        ObjectError: 对象不存在、方言不支持、routine_type 非法。
    """
    if routine_type not in _ROUTINE_TYPES:
        raise ObjectError(f"routine_type 须为 {sorted(_ROUTINE_TYPES)}，实际: {routine_type}")
    dialect = engine.dialect.name
    if dialect == EngineType.SQLITE:
        raise ObjectError("SQLite 不支持存储过程/函数")
    if dialect == EngineType.MYSQL:
        keyword = "PROCEDURE" if routine_type == ROUTINE_PROCEDURE else "FUNCTION"
        with engine.connect() as conn:
            rows = conn.execute(text(f"SHOW CREATE {keyword} {name}")).fetchall()
        if not rows:
            raise ObjectError(f"{routine_type} {name} 不存在")
        # SHOW CREATE PROCEDURE 返回 (Routine, sql_mode, Create Procedure, ...)
        # 取第 3 列（Create Procedure/Create Function）
        return cast(str, rows[0][2])
    if dialect == EngineType.POSTGRESQL:
        sql = _ROUTINE_DEF_SQL[EngineType.POSTGRESQL]
        params = {"schema": schema or "public", "name": name}
        with engine.connect() as conn:
            row = conn.execute(text(sql), params).fetchone()
        if row is None or row[0] is None:
            raise ObjectError(f"{routine_type} {name} 不存在")
        return cast(str, row[0])
    raise ObjectError(f"方言 {dialect} 暂不支持存储过程/函数反射")  # pragma: no cover


def alter_routine(
    engine: Engine,
    name: str,
    schema: str | None,  # noqa: ARG001
    definition: str,
    routine_type: str,
) -> None:
    """编辑存储过程/函数：DROP IF EXISTS + CREATE（单事务）.

    MySQL 用 ``DROP PROCEDURE/FUNCTION IF EXISTS`` + ``CREATE``；
    PG 函数可用 ``CREATE OR REPLACE FUNCTION``，但存储过程（PG 11+）用 DROP + CREATE。
    为统一行为，一律采用 DROP IF EXISTS + CREATE 方案。

    Args:
        engine: SQLAlchemy 引擎。
        name: 对象名。
        schema: Schema 名。
        definition: 完整的 CREATE PROCEDURE/FUNCTION 语句。
        routine_type: ``procedure`` 或 ``function``。

    Raises:
        ObjectError: definition 为空/非 CREATE 语句、routine_type 非法、方言不支持。
        SQLAlchemyError: 底层执行失败。
    """
    if routine_type not in _ROUTINE_TYPES:
        raise ObjectError(f"routine_type 须为 {sorted(_ROUTINE_TYPES)}，实际: {routine_type}")
    dialect = engine.dialect.name
    if dialect == EngineType.SQLITE:
        raise ObjectError("SQLite 不支持存储过程/函数")
    keyword = "PROCEDURE" if routine_type == ROUTINE_PROCEDURE else "FUNCTION"
    cleaned = _clean_definition(definition, f"CREATE {keyword}")
    # MySQL/PG 均支持 DROP PROCEDURE/FUNCTION IF EXISTS
    # PG 删除函数需带参数签名（如 DROP FUNCTION name(int)），但此处简化为按名删除
    # 注：若存在同名不同参的重载，按名删除会失败，由调用方确保定义完整
    drop_sql = f"DROP {keyword} IF EXISTS {name}"
    with engine.begin() as conn:
        conn.execute(text(drop_sql))
        conn.execute(text(cleaned))


def drop_routine(
    engine: Engine,
    name: str,
    schema: str | None,  # noqa: ARG001
    routine_type: str,
) -> None:
    """删除存储过程/函数.

    Raises:
        ObjectError: routine_type 非法、方言不支持。
        SQLAlchemyError: 底层执行失败。
    """
    if routine_type not in _ROUTINE_TYPES:
        raise ObjectError(f"routine_type 须为 {sorted(_ROUTINE_TYPES)}，实际: {routine_type}")
    dialect = engine.dialect.name
    if dialect == EngineType.SQLITE:
        raise ObjectError("SQLite 不支持存储过程/函数")
    keyword = "PROCEDURE" if routine_type == ROUTINE_PROCEDURE else "FUNCTION"
    with engine.begin() as conn:
        conn.execute(text(f"DROP {keyword} IF EXISTS {name}"))


# ============================================================
# 触发器
# ============================================================


# MySQL: information_schema.triggers
# PG: information_schema.triggers（部分字段）+ pg_trigger（完整定义需 pg_get_triggerdef）
# SQLite: sqlite_master
_TRIGGER_LIST_SQL: dict[str, str] = {
    EngineType.MYSQL: (
        "SELECT trigger_name, event_manipulation, event_object_table, action_timing "
        "FROM information_schema.triggers "
        "WHERE trigger_schema = DATABASE() ORDER BY trigger_name"
    ),
    EngineType.POSTGRESQL: (
        "SELECT trigger_name, event_manipulation, event_object_table, action_timing "
        "FROM information_schema.triggers "
        "WHERE trigger_schema = :schema ORDER BY trigger_name"
    ),
    EngineType.SQLITE: (
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ),
}


def list_triggers(engine: Engine, schema: str | None = None) -> list[TriggerMeta]:
    """列出触发器.

    Args:
        engine: SQLAlchemy 引擎。
        schema: Schema 名。

    Returns:
        ``TriggerMeta`` 列表（definition 字段为空，需单独获取）。
    """
    dialect = engine.dialect.name
    sql = _TRIGGER_LIST_SQL.get(dialect)
    if sql is None:  # pragma: no cover
        raise ObjectError(f"方言 {dialect} 暂不支持触发器反射")
    params: dict[str, Any] = {}
    if dialect == EngineType.POSTGRESQL:
        params["schema"] = schema or "public"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    result: list[TriggerMeta] = []
    for row in rows:
        tname = cast(str, row[0])
        if dialect == EngineType.SQLITE:
            # SQLite: (name, tbl_name, sql)
            table = cast(str, row[1])
            # SQLite 触发器 timing/event 需解析 sql，此处简化为空
            result.append(TriggerMeta(name=tname, schema=schema, event="", table=table, timing="", definition=""))
        else:
            event = cast(str, row[1])
            table = cast(str, row[2])
            timing = cast(str, row[3])
            result.append(
                TriggerMeta(name=tname, schema=schema, event=event, table=table, timing=timing, definition="")
            )
    return result


# 触发器定义 SQL
_TRIGGER_DEF_SQL: dict[str, str] = {
    EngineType.MYSQL: "",  # 用 SHOW CREATE TRIGGER
    EngineType.POSTGRESQL: ("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :name AND NOT tgisinternal"),
    EngineType.SQLITE: "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name",
}


def get_trigger_definition(
    engine: Engine,
    name: str,
    schema: str | None = None,  # noqa: ARG001
) -> str:
    """获取触发器定义 SQL.

    Raises:
        ObjectError: 触发器不存在或方言不支持。
    """
    dialect = engine.dialect.name
    if dialect == EngineType.MYSQL:
        with engine.connect() as conn:
            rows = conn.execute(text(f"SHOW CREATE TRIGGER {name}")).fetchall()
        if not rows:
            raise ObjectError(f"触发器 {name} 不存在")
        # SHOW CREATE TRIGGER 返回多列，最后一列为 SQL Original Statement
        # 列顺序: Trigger, sql_mode, SQL Original Statement, ...
        return cast(str, rows[0][2])
    sql = _TRIGGER_DEF_SQL.get(dialect)
    if sql is None:  # pragma: no cover
        raise ObjectError(f"方言 {dialect} 暂不支持触发器反射")
    params: dict[str, Any] = {"name": name}
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).fetchone()
    if row is None or row[0] is None:
        raise ObjectError(f"触发器 {name} 不存在")
    return cast(str, row[0])


def alter_trigger(
    engine: Engine,
    name: str,
    schema: str | None,
    definition: str,
    table: str | None = None,
) -> None:
    """编辑触发器：DROP IF EXISTS + CREATE（单事务）.

    Args:
        engine: SQLAlchemy 引擎。
        name: 触发器名。
        schema: Schema 名。
        definition: 完整的 CREATE TRIGGER 语句。
        table: 关联表名（PG ``DROP TRIGGER ... ON table`` 需要；MySQL/SQLite 忽略）。

    Raises:
        ObjectError: definition 为空/非 CREATE TRIGGER、PG 缺少 table。
        SQLAlchemyError: 底层执行失败。
    """
    cleaned = _clean_definition(definition, "CREATE TRIGGER")
    dialect = engine.dialect.name
    drop_sql = _build_trigger_drop_sql(dialect, name, schema, table)
    with engine.begin() as conn:
        conn.execute(text(drop_sql))
        conn.execute(text(cleaned))


def drop_trigger(
    engine: Engine,
    name: str,
    schema: str | None,
    table: str | None = None,
) -> None:
    """删除触发器.

    Raises:
        ObjectError: PG 缺少 table。
        SQLAlchemyError: 底层执行失败。
    """
    dialect = engine.dialect.name
    drop_sql = _build_trigger_drop_sql(dialect, name, schema, table, if_exists=True)
    with engine.begin() as conn:
        conn.execute(text(drop_sql))


def _build_trigger_drop_sql(
    dialect: str,
    name: str,
    schema: str | None,
    table: str | None,
    *,
    if_exists: bool = True,
) -> str:
    """构造触发器 DROP 语句.

    - MySQL: ``DROP TRIGGER [IF EXISTS] schema.name``
    - PG: ``DROP TRIGGER [IF EXISTS] name ON schema.table``
    - SQLite: ``DROP TRIGGER [IF EXISTS] name``

    Raises:
        ObjectError: PG 缺少 table。
    """
    exists_clause = "IF EXISTS " if if_exists else ""
    if dialect == EngineType.MYSQL:
        effective_schema = schema
        ref = _format_table_ref(name, effective_schema, dialect)
        return f"DROP TRIGGER {exists_clause}{ref}"
    if dialect == EngineType.POSTGRESQL:
        if not table:
            raise ObjectError("PG 删除触发器需要关联表名 table")
        table_ref = _format_table_ref(table, schema, dialect)
        return f"DROP TRIGGER {exists_clause}{name} ON {table_ref}"
    if dialect == EngineType.SQLITE:
        return f"DROP TRIGGER {exists_clause}{_quote_ident(name, dialect)}"
    raise ObjectError(f"方言 {dialect} 暂不支持触发器反射")  # pragma: no cover


# ============================================================
# 公共工具
# ============================================================


def _clean_definition(definition: str, expected_prefix: str) -> str:
    """清洗并校验定义 SQL.

    去除首尾空白与末尾分号；校验是否以预期前缀开头（不区分大小写）。

    Args:
        definition: 原始定义 SQL。
        expected_prefix: 预期前缀，如 ``CREATE VIEW``。

    Returns:
        清洗后的 SQL。

    Raises:
        ObjectError: 定义为空或前缀不匹配。
    """
    cleaned = definition.strip().rstrip(";").strip()
    if not cleaned:
        raise ObjectError("定义 SQL 不能为空")
    if not cleaned.upper().startswith(expected_prefix.upper()):
        raise ObjectError(f"定义 SQL 须以 {expected_prefix} 开头")
    return cleaned


__all__ = [
    "ROUTINE_FUNCTION",
    "ROUTINE_PROCEDURE",
    "ObjectError",
    "RoutineMeta",
    "TriggerMeta",
    "ViewMeta",
    "alter_routine",
    "alter_trigger",
    "alter_view",
    "drop_routine",
    "drop_trigger",
    "drop_view",
    "get_routine_definition",
    "get_trigger_definition",
    "get_view_definition",
    "list_routines",
    "list_triggers",
    "list_views",
]
