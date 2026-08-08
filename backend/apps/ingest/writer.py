"""数据写入器 — 方言化 UPSERT/SKIP/ERROR 批量写入.

将爬取的行按冲突策略写入目标数据源的目标表。方言实现与 sync_service 一致：
- MySQL: INSERT ... ON DUPLICATE KEY UPDATE / INSERT IGNORE
- PostgreSQL: INSERT ... ON CONFLICT (pk) DO UPDATE/NOTHING
- SQLite: INSERT ... ON CONFLICT(pk) DO UPDATE/NOTHING

写入逻辑独立于 sync_service，保持模块自洽；待第三处相似出现时提取公共 writer。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.datasources.models import EngineType
from apps.ingest.models import ConflictStrategy

logger = logging.getLogger(__name__)


def write_rows(  # noqa: PLR0913
    engine: Engine,
    rows: list[dict[str, Any]],
    *,
    target_table: str,
    target_fields: list[str],
    pk_fields: list[str],
    conflict_strategy: str,
) -> tuple[int, int]:
    """批量写入目标表，返回 (written, skipped).

    Args:
        engine: 目标数据源的 SQLAlchemy 引擎。
        rows: 已完成字段映射的行列表（键为目标字段名）。
        target_table: 目标表名。
        target_fields: 全部目标字段名（有序）。
        pk_fields: 主键字段名列表（用于冲突判定）。
        conflict_strategy: 冲突策略（upsert/skip/error）。

    Returns:
        (written_count, skipped_count)。ERROR 策略下任一行冲突即抛出异常。
    """
    if not rows:
        return 0, 0

    dialect = engine.dialect.name
    table_ref = _format_table_ref(target_table, dialect)
    non_pk_fields = [f for f in target_fields if f not in pk_fields]

    written = 0
    skipped = 0

    try:
        with engine.begin() as conn:
            for row in rows:
                if conflict_strategy == ConflictStrategy.ERROR:
                    _insert_only(conn, table_ref, dialect, target_fields, row)
                    written += 1
                    continue
                try:
                    row_written = _write_single_row(
                        conn, table_ref, dialect, conflict_strategy, target_fields, pk_fields, non_pk_fields, row
                    )
                    if row_written:
                        written += 1
                    else:
                        skipped += 1
                except Exception as exc:  # 单行写入异常计入跳过，不中断整批
                    logger.warning("写入行失败: pk=%s, error=%s", {k: row.get(k) for k in pk_fields}, exc)
                    skipped += 1
    except Exception as exc:
        raise ValueError(f"写入目标表失败: {exc}") from exc

    return written, skipped


def _write_single_row(  # noqa: PLR0913 PLR0917
    conn: Any,
    table_ref: str,
    dialect: str,
    strategy: str,
    all_fields: list[str],
    pk_fields: list[str],
    non_pk_fields: list[str],
    row: dict[str, Any],
) -> bool:
    """按冲突策略写入单行，返回是否实际写入（SKIP 冲突时为 False）."""
    if not pk_fields:
        _insert_only(conn, table_ref, dialect, all_fields, row)
        return True
    if strategy == ConflictStrategy.SKIP:
        return _skip_single_row(conn, table_ref, dialect, all_fields, pk_fields, row)
    _upsert_single_row(conn, table_ref, dialect, all_fields, pk_fields, non_pk_fields, row)
    return True


def _upsert_single_row(  # noqa: PLR0913 PLR0917
    conn: Any,
    table_ref: str,
    dialect: str,
    all_fields: list[str],
    pk_fields: list[str],
    non_pk_fields: list[str],
    row: dict[str, Any],
) -> None:
    """对单行执行 UPSERT（各方言 ON CONFLICT DO UPDATE）."""
    if dialect == EngineType.MYSQL:
        _upsert_mysql(conn, table_ref, all_fields, non_pk_fields, row)
    elif dialect == EngineType.POSTGRESQL:
        _upsert_postgresql(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
    elif dialect == EngineType.SQLITE:
        _upsert_sqlite(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
    else:
        _insert_only(conn, table_ref, dialect, all_fields, row)


def _skip_single_row(  # noqa: PLR0913 PLR0917
    conn: Any,
    table_ref: str,
    dialect: str,
    all_fields: list[str],
    pk_fields: list[str],
    row: dict[str, Any],
) -> bool:
    """对单行执行冲突则跳过（INSERT IGNORE / ON CONFLICT DO NOTHING）.

    Returns:
        是否实际插入（rowcount != 0），冲突跳过时为 False。
    """
    col_refs = ", ".join(_quote_ident(c, dialect) for c in all_fields)
    placeholders = ", ".join(f":{c}" for c in all_fields)
    params = {c: row.get(c) for c in all_fields}

    if dialect == EngineType.MYSQL:
        sql = f"INSERT IGNORE INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
    elif dialect in (EngineType.POSTGRESQL, EngineType.SQLITE):
        pk_refs = ", ".join(_quote_ident(c, dialect) for c in pk_fields)
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) ON CONFLICT ({pk_refs}) DO NOTHING"
    else:
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"

    cursor = conn.execute(text(sql), params)
    return cursor.rowcount != 0


def _upsert_mysql(
    conn: Any,
    table_ref: str,
    all_fields: list[str],
    non_pk_fields: list[str],
    row: dict[str, Any],
) -> None:
    """MySQL UPSERT: INSERT ... ON DUPLICATE KEY UPDATE."""
    col_refs = ", ".join(_quote_ident(c, EngineType.MYSQL) for c in all_fields)
    placeholders = ", ".join(f":{c}" for c in all_fields)
    set_clause = ", ".join(
        f"{_quote_ident(c, EngineType.MYSQL)}=VALUES({_quote_ident(c, EngineType.MYSQL)})" for c in non_pk_fields
    )
    sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
    if set_clause:
        sql += f" ON DUPLICATE KEY UPDATE {set_clause}"
    conn.execute(text(sql), {c: row.get(c) for c in all_fields})


def _upsert_postgresql(  # noqa: PLR0913 PLR0917
    conn: Any,
    table_ref: str,
    all_fields: list[str],
    pk_fields: list[str],
    non_pk_fields: list[str],
    row: dict[str, Any],
) -> None:
    """PostgreSQL UPSERT: INSERT ... ON CONFLICT (pk) DO UPDATE SET ..."""
    col_refs = ", ".join(_quote_ident(c, EngineType.POSTGRESQL) for c in all_fields)
    placeholders = ", ".join(f":{c}" for c in all_fields)
    pk_refs = ", ".join(_quote_ident(c, EngineType.POSTGRESQL) for c in pk_fields)
    set_clause = ", ".join(
        f"{_quote_ident(c, EngineType.POSTGRESQL)}=EXCLUDED.{_quote_ident(c, EngineType.POSTGRESQL)}"
        for c in non_pk_fields
    )
    sql = (
        f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk_refs}) DO UPDATE SET {set_clause}"
    )
    conn.execute(text(sql), {c: row.get(c) for c in all_fields})


def _upsert_sqlite(  # noqa: PLR0913 PLR0917
    conn: Any,
    table_ref: str,
    all_fields: list[str],
    pk_fields: list[str],
    non_pk_fields: list[str],
    row: dict[str, Any],
) -> None:
    """SQLite UPSERT: INSERT ... ON CONFLICT(pk) DO UPDATE SET ..."""
    col_refs = ", ".join(_quote_ident(c, EngineType.SQLITE) for c in all_fields)
    placeholders = ", ".join(f":{c}" for c in all_fields)
    pk_refs = ", ".join(_quote_ident(c, EngineType.SQLITE) for c in pk_fields)
    set_clause = ", ".join(
        f"{_quote_ident(c, EngineType.SQLITE)}=excluded.{_quote_ident(c, EngineType.SQLITE)}" for c in non_pk_fields
    )
    sql = (
        f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk_refs}) DO UPDATE SET {set_clause}"
    )
    conn.execute(text(sql), {c: row.get(c) for c in all_fields})


def _insert_only(
    conn: Any,
    table_ref: str,
    dialect: str,
    all_fields: list[str],
    row: dict[str, Any],
) -> None:
    """纯 INSERT（无主键或 ERROR 策略）."""
    col_refs = ", ".join(_quote_ident(c, dialect) for c in all_fields)
    placeholders = ", ".join(f":{c}" for c in all_fields)
    sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
    conn.execute(text(sql), {c: row.get(c) for c in all_fields})


def _format_table_ref(table: str, dialect: str) -> str:
    """构造目标表引用（方言化引号）."""
    quote = "`" if dialect == EngineType.MYSQL else '"'
    return f"{quote}{table}{quote}"


def _quote_ident(name: str, dialect: str) -> str:
    """标识符引用（方言化）."""
    if dialect == EngineType.MYSQL:
        return f"`{name}`"
    return f'"{name}"'


__all__ = ["write_rows"]
