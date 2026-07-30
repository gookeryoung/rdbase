"""SQLAlchemy 元数据反射层.

封装 SQLAlchemy inspect API，统一返回库/Schema/表/字段元数据，
屏蔽 MySQL/PostgreSQL/SQLite 方言差异。

设计要点：
- ``list_databases``: MySQL/PostgreSQL 用 SQL 查询服务器上的数据库列表；SQLite 返回 ``['main']``
- ``list_schemas``: 封装 SQLAlchemy ``get_schema_names``；SQLite 返回 ``['main']``
- ``list_tables`` / ``list_views``: 封装 ``get_table_names`` / ``get_view_names``
- ``inspect_table``: 一次取列/主键/外键/索引/唯一约束/注释，合并为 ``TableMeta``

元数据返回值对象均使用 ``frozen=True`` dataclass，便于缓存与序列化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from apps.datasources.models import EngineType


@dataclass(frozen=True)
class ColumnMeta:
    """字段元数据."""

    name: str
    type: str
    nullable: bool
    default: str | None = None
    autoincrement: bool = False
    comment: str | None = None
    primary_key: bool = False
    unique: bool = False


@dataclass(frozen=True)
class IndexMeta:
    """索引元数据."""

    name: str
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True)
class ForeignKeyMeta:
    """外键元数据."""

    name: str | None
    columns: tuple[str, ...]
    referred_table: str
    referred_schema: str | None
    referred_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableMeta:
    """表完整元数据."""

    name: str
    schema: str | None
    comment: str | None
    columns: tuple[ColumnMeta, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyMeta, ...]
    indexes: tuple[IndexMeta, ...]
    unique_constraints: tuple[tuple[str, ...], ...]


# 各方言查询数据库列表的 SQL（SQLite 走分支返回 ['main']）
_DATABASE_QUERIES: dict[str, str] = {
    EngineType.MYSQL: "SHOW DATABASES",
    EngineType.POSTGRESQL: (
        "SELECT datname FROM pg_database WHERE datname NOT LIKE 'template%' AND datname <> 'postgres' ORDER BY datname"
    ),
}


def _resolve_schema(engine: Engine, schema: str | None) -> str | None:
    """SQLite 不支持 schema 概念，强制返回 None；其他方言原样返回."""
    if engine.dialect.name == EngineType.SQLITE:
        return None
    return schema


def list_databases(engine: Engine) -> list[str]:
    """列出当前服务器上的所有数据库.

    MySQL/PostgreSQL 通过 SQL 查询；SQLite 仅返回 ``['main']``；
    未知方言退化为 SQLAlchemy ``get_schema_names``。
    """
    dialect = engine.dialect.name
    if dialect == EngineType.SQLITE:
        return ["main"]
    sql = _DATABASE_QUERIES.get(dialect)
    if sql is None:
        # 未知方言退化为 SQLAlchemy get_schema_names
        return list(inspect(engine).get_schema_names())  # pragma: no cover
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return [cast(str, row[0]) for row in rows if row[0]]


def list_schemas(engine: Engine) -> list[str]:
    """列出当前数据库的 Schema 列表.

    SQLite 返回 ``['main']``；其他方言封装 SQLAlchemy ``get_schema_names``。
    """
    if engine.dialect.name == EngineType.SQLITE:
        return ["main"]
    return [cast(str, name) for name in inspect(engine).get_schema_names()]


def list_tables(engine: Engine, schema: str | None = None) -> list[str]:
    """列出指定 Schema 下的所有表名."""
    insp = inspect(engine)
    effective_schema = _resolve_schema(engine, schema)
    return [cast(str, name) for name in insp.get_table_names(schema=effective_schema)]


def list_views(engine: Engine, schema: str | None = None) -> list[str]:
    """列出指定 Schema 下的所有视图名.

    某些方言不支持视图反射时返回空列表。
    """
    insp = inspect(engine)
    effective_schema = _resolve_schema(engine, schema)
    try:
        return [cast(str, name) for name in insp.get_view_names(schema=effective_schema)]
    except NotImplementedError:  # pragma: no cover - 部分方言不支持视图反射
        return []


def inspect_table(engine: Engine, table_name: str, schema: str | None = None) -> TableMeta:
    """读取单张表的完整元数据（字段/主键/外键/索引/唯一约束/注释）.

    Raises:
        NoSuchTableError: 表不存在时由 SQLAlchemy 抛出。
    """
    insp = inspect(engine)
    effective_schema = _resolve_schema(engine, schema)

    raw_columns = insp.get_columns(table_name, schema=effective_schema)
    pk_constraint = insp.get_pk_constraint(table_name, schema=effective_schema)
    pk_columns = set(pk_constraint.get("constrained_columns") or [])

    raw_unique = insp.get_unique_constraints(table_name, schema=effective_schema)
    # 单列唯一约束合并到 ColumnMeta.unique；多列保留在 unique_constraints
    single_unique_cols: set[str] = set()
    multi_unique_groups: list[tuple[str, ...]] = []
    for uc in raw_unique:
        cols = tuple(c for c in (uc.get("column_names") or []) if c)
        if not cols:
            continue
        if len(cols) == 1:
            single_unique_cols.add(cols[0])
        else:
            multi_unique_groups.append(cols)

    raw_indexes = insp.get_indexes(table_name, schema=effective_schema)
    # 单列唯一索引（如 SQLite 列级 UNIQUE 触发的隐式索引）也合并到 ColumnMeta.unique
    for idx in raw_indexes:
        idx_cols = [c for c in (idx.get("column_names") or []) if c]
        if idx.get("unique") and len(idx_cols) == 1:
            single_unique_cols.add(idx_cols[0])

    columns: list[ColumnMeta] = []
    for col in raw_columns:
        name = cast(str, col["name"])
        default = col.get("default")
        comment = col.get("comment")
        columns.append(
            ColumnMeta(
                name=name,
                type=str(col.get("type", "")),
                nullable=bool(col.get("nullable", True)),
                default=str(default) if default is not None else None,
                autoincrement=bool(col.get("autoincrement", False)),
                comment=cast("str | None", comment) if comment else None,
                primary_key=name in pk_columns,
                unique=name in single_unique_cols,
            )
        )

    raw_fks = insp.get_foreign_keys(table_name, schema=effective_schema)
    foreign_keys: list[ForeignKeyMeta] = []
    for fk in raw_fks:
        foreign_keys.append(
            ForeignKeyMeta(
                name=cast("str | None", fk.get("name")),
                columns=tuple(c for c in (fk.get("constrained_columns") or []) if c),
                referred_table=cast(str, fk.get("referred_table", "")),
                referred_schema=cast("str | None", fk.get("referred_schema")),
                referred_columns=tuple(c for c in (fk.get("referred_columns") or []) if c),
            )
        )

    indexes: list[IndexMeta] = []
    for idx in raw_indexes:
        indexes.append(
            IndexMeta(
                name=cast(str, idx.get("name", "")),
                columns=tuple(c for c in (idx.get("column_names") or []) if c),
                unique=bool(idx.get("unique", False)),
            )
        )

    try:
        comment_dict = insp.get_table_comment(table_name, schema=effective_schema)
        table_comment = cast("str | None", comment_dict.get("text")) or None
    except NotImplementedError:  # pragma: no cover - SQLite 不支持表注释
        table_comment = None

    return TableMeta(
        name=table_name,
        schema=effective_schema,
        comment=table_comment,
        columns=tuple(columns),
        primary_key=tuple(pk_columns),
        foreign_keys=tuple(foreign_keys),
        indexes=tuple(indexes),
        unique_constraints=tuple(multi_unique_groups),
    )


__all__ = [
    "ColumnMeta",
    "ForeignKeyMeta",
    "IndexMeta",
    "TableMeta",
    "inspect_table",
    "list_databases",
    "list_schemas",
    "list_tables",
    "list_views",
]
