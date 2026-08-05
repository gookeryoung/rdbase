"""数据同步服务.

实现从 rdbase 平台库向外部数据源推送数据的核心逻辑。
支持全量同步（推送源表全部数据）与增量同步（按 updated_at 时间戳筛选变更行）。

同步策略：
1. 从 Django 默认数据库（或指定 alias）读取源表数据
2. 按字段映射转换数据
3. 使用各方言 UPSERT 语义（存在则更新，不存在则插入）写入目标表
4. 记录同步日志

各方言 UPSERT 实现：
- MySQL: INSERT ... ON DUPLICATE KEY UPDATE
- PostgreSQL: INSERT ... ON CONFLICT (pk) DO UPDATE SET ...
- SQLite (>=3.24): INSERT ... ON CONFLICT(pk) DO UPDATE SET ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import connection
from django.utils import timezone
from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.datasources.engine import get_engine as get_ds_engine
from apps.datasources.models import EngineType

from .models import (
    SyncConfig,
    SyncFieldMapping,
    SyncLog,
    SyncLogStatus,
    SyncMode,
    SyncStatus,
)
from .scheduling import compute_next_run, is_valid_cron

logger = logging.getLogger(__name__)


class SyncError(ValueError):
    """同步错误."""


@dataclass
class SyncPreview:
    """同步预览结果."""

    config_id: int
    config_name: str
    mode: str
    total_rows: int
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    target_fields: list[str] = field(default_factory=list)
    pk_fields: list[str] = field(default_factory=list)
    can_sync: bool = True
    error_message: str = ""


@dataclass
class BatchSyncResult:
    """批量同步结果."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[SyncLog] = field(default_factory=list)


class SyncService:
    """数据同步服务.

    每个 SyncService 实例绑定一个 SyncConfig，通过 :meth:`run` 执行同步。
    设计为无状态服务类，便于测试与复用。
    """

    def __init__(self, config: SyncConfig) -> None:
        self.config = config

    # ================================================================
    # 公共入口
    # ================================================================

    def run(self, *, force_full: bool = False, max_retries: int | None = None) -> SyncLog:
        """执行同步.

        Args:
            force_full: 强制全量同步（忽略配置的 sync_mode）。
            max_retries: 最大重试次数，None 则使用配置中的值。

        Returns:
            SyncLog: 同步执行日志。

        Raises:
            SyncError: 配置无效、源表不存在、目标不可达等。
        """
        config = self.config
        max_attempts = max_retries if max_retries is not None else config.max_retries
        last_exception: Exception | None = None

        for attempt in range(max_attempts + 1):
            try:
                return self._do_run(force_full=force_full)
            except SyncError as exc:
                last_exception = exc
                config.retry_count = attempt + 1
                config.save(update_fields=["retry_count"])

                if attempt < max_attempts:
                    logger.warning(
                        "同步失败，第 %d/%d 次重试: config=%s, error=%s",
                        attempt + 1,
                        max_attempts,
                        config.name,
                        exc,
                    )
                else:
                    logger.error(
                        "同步失败（已达最大重试次数）: config=%s, error=%s",
                        config.name,
                        exc,
                    )

        raise SyncError(str(last_exception)) if last_exception else SyncError("未知错误")

    def _do_run(self, *, force_full: bool = False) -> SyncLog:
        """执行单次同步（内部方法）."""
        config = self.config
        started_at = timezone.now()
        mode = SyncMode.FULL if force_full else config.sync_mode

        log = SyncLog.objects.create(
            config=config,
            status=SyncLogStatus.FAILED,
            mode=mode,
            rows_read=0,
            rows_written=0,
            rows_skipped=0,
            started_at=started_at,
        )

        try:
            mappings = list(config.field_mappings.all())
            if not mappings:
                raise SyncError("未配置字段映射")

            source_rows = self._read_source_data(mode)
            rows_read = len(source_rows)

            if rows_read == 0:
                self._finalize_log(log, SyncLogStatus.SUCCESS, 0, 0, started_at, "无待同步数据")
                config.last_sync_at = started_at
                config.retry_count = 0
                config.save(update_fields=["last_sync_at", "retry_count"])
                return log

            converted_rows = self._apply_mappings(source_rows, mappings)

            ds_engine = get_ds_engine(config.target_datasource)
            written, skipped = self._write_target_data(ds_engine, converted_rows, mappings)

            self._finalize_log(log, SyncLogStatus.SUCCESS, rows_read, written, started_at)
            log.rows_skipped = skipped
            log.save()

            config.last_sync_at = started_at
            config.retry_count = 0
            config.status = SyncStatus.ACTIVE
            config.save(update_fields=["last_sync_at", "retry_count", "status"])

            logger.info(
                "同步完成: config=%s, mode=%s, read=%d, written=%d, skipped=%d",
                config.name,
                mode,
                rows_read,
                written,
                skipped,
            )

        except Exception as exc:
            elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
            log.status = SyncLogStatus.FAILED
            log.error_message = str(exc)
            log.finished_at = timezone.now()
            log.duration_ms = elapsed_ms
            log.save()

            config.status = SyncStatus.ERROR
            config.save(update_fields=["status"])

            logger.error("同步失败: config=%s, error=%s", config.name, exc)
            raise SyncError(str(exc)) from exc

        return log

    # ================================================================
    # 预览与统计
    # ================================================================

    def preview(self, *, force_full: bool = False, sample_size: int = 5) -> SyncPreview:
        """预览将要同步的数据.

        Args:
            force_full: 强制全量同步。
            sample_size: 采样行数。

        Returns:
            SyncPreview: 预览结果。
        """
        config = self.config
        mode = SyncMode.FULL if force_full else config.sync_mode

        mappings = list(config.field_mappings.all())
        if not mappings:
            return SyncPreview(
                config_id=config.pk,
                config_name=config.name,
                mode=mode,
                total_rows=0,
                can_sync=False,
                error_message="未配置字段映射",
            )

        try:
            source_rows = self._read_source_data(mode)
            converted_rows = self._apply_mappings(source_rows, mappings)
            total_rows = len(converted_rows)
            sample = converted_rows[:sample_size] if sample_size > 0 else []

            target_fields = [m.target_field for m in mappings]
            pk_fields = [m.target_field for m in mappings if m.is_pk]

            return SyncPreview(
                config_id=config.pk,
                config_name=config.name,
                mode=mode,
                total_rows=total_rows,
                sample_rows=sample,
                target_fields=target_fields,
                pk_fields=pk_fields,
                can_sync=True,
            )
        except Exception as exc:
            return SyncPreview(
                config_id=config.pk,
                config_name=config.name,
                mode=mode,
                total_rows=0,
                can_sync=False,
                error_message=str(exc),
            )

    def get_source_count(self, *, force_full: bool = False) -> int:
        """获取源表数据量.

        Args:
            force_full: 强制全量同步。

        Returns:
            int: 数据行数。
        """
        mode = SyncMode.FULL if force_full else self.config.sync_mode
        source_rows = self._read_source_data(mode)
        return len(source_rows)

    # ================================================================
    # 批量同步
    # ================================================================

    @staticmethod
    def run_batch(
        config_ids: list[int],
        *,
        force_full: bool = False,
        stop_on_error: bool = False,
    ) -> BatchSyncResult:
        """批量执行同步.

        Args:
            config_ids: 同步配置 ID 列表。
            force_full: 强制全量同步。
            stop_on_error: 遇错即停。

        Returns:
            BatchSyncResult: 批量同步结果。
        """
        result = BatchSyncResult(total=len(config_ids))

        configs = SyncConfig.objects.filter(pk__in=config_ids, status=SyncStatus.ACTIVE)
        config_map = {c.pk: c for c in configs}

        for config_id in config_ids:
            config = config_map.get(config_id)
            if config is None:
                result.skipped += 1
                continue

            if not config.is_active:
                result.skipped += 1
                continue

            try:
                service = SyncService(config)
                log = service.run(force_full=force_full)
                result.succeeded += 1
                result.results.append(log)
            except SyncError:
                result.failed += 1
                if stop_on_error:
                    break

        return result

    @staticmethod
    def run_scheduled() -> BatchSyncResult:
        """执行所有可调度的配置.

        查找所有启用了定时调度且到达执行时间的配置并执行，执行后基于
        cron 表达式滚动更新 next_run_at，使调度可自动循环。

        Returns:
            BatchSyncResult: 批量同步结果。
        """
        now = timezone.now()
        configs = SyncConfig.objects.filter(
            status=SyncStatus.ACTIVE,
            scheduler_enabled=True,
            next_run_at__lte=now,
        )

        if not configs:
            return BatchSyncResult(total=0)

        result = BatchSyncResult(total=configs.count())

        for config in configs:
            try:
                service = SyncService(config)
                log = service._do_run()
                result.succeeded += 1
                result.results.append(log)
            except SyncError:
                result.failed += 1
            finally:
                # 无论成功失败都基于 cron 滚动到下次执行时间，避免过期任务反复触发。
                # 注意：不依赖 is_active（失败会将 status 置为 ERROR），仅要求
                # 启用调度且 cron 合法，从而使定时循环在单次失败后仍能持续。
                config.last_run_at = now
                if config.scheduler_enabled and is_valid_cron(config.cron_expression):
                    config.next_run_at = compute_next_run(config.cron_expression, base=now)
                config.save(update_fields=["last_run_at", "next_run_at"])

        return result

    # ================================================================
    # 源数据读取
    # ================================================================

    def _read_source_data(self, mode: str) -> list[dict[str, Any]]:
        """从 rdbase 平台库读取源表数据."""
        config = self.config
        db_alias = config.source_db_alias or "default"

        # 获取 Django 数据库连接
        try:
            with connection.cursor() as _cursor:
                pass  # 验证连接可用
        except Exception as exc:
            raise SyncError(f"无法连接数据库（alias={db_alias}）: {exc}") from exc

        # 构造 SQL
        table_ref = self._quote_ident(config.source_table, "sqlite")  # rdbase 默认 SQLite
        sql = f"SELECT * FROM {table_ref}"
        params: dict[str, Any] = {}

        if mode == SyncMode.INCREMENTAL and config.last_sync_at and config.timestamp_field:
            ts_field = self._quote_ident(config.timestamp_field, "sqlite")
            sql += f" WHERE {ts_field} > :last_sync"
            params["last_sync"] = config.last_sync_at.isoformat()

        sql += f" ORDER BY {self._quote_ident('id', 'sqlite')} ASC"

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                columns = [col[0] for col in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
        except Exception as exc:
            raise SyncError(f"读取源表 {config.source_table} 失败: {exc}") from exc

        # 转为 dict 列表
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(dict(zip(columns, row, strict=False)))
        return result

    # ================================================================
    # 字段映射转换
    # ================================================================

    def _apply_mappings(
        self,
        source_rows: list[dict[str, Any]],
        mappings: list[SyncFieldMapping],
    ) -> list[dict[str, Any]]:
        """按字段映射将源行转换为目标行."""
        result: list[dict[str, Any]] = []
        for src_row in source_rows:
            target_row: dict[str, Any] = {}
            for m in mappings:
                if m.mapping_type == "constant":
                    # 常量映射：使用 fixed_value
                    target_row[m.target_field] = m.fixed_value
                else:
                    # 直接映射：从源字段读取
                    val = src_row.get(m.source_field)
                    target_row[m.target_field] = val
            result.append(target_row)
        return result

    # ================================================================
    # 目标数据写入（UPSERT）
    # ================================================================

    def _write_target_data(
        self,
        engine: Engine,
        rows: list[dict[str, Any]],
        mappings: list[SyncFieldMapping],
    ) -> tuple[int, int]:
        """批量写入目标表（UPSERT）.

        Returns:
            (written_count, skipped_count)
        """
        if not rows:
            return 0, 0

        pk_fields = [m.target_field for m in mappings if m.is_pk]
        non_pk_fields = [m.target_field for m in mappings if not m.is_pk]
        all_fields = [m.target_field for m in mappings]

        dialect = engine.dialect.name
        table_ref = self._format_target_table_ref(dialect)

        # 批量 upsert
        written = 0
        skipped = 0

        try:
            with engine.begin() as conn:
                for row in rows:
                    try:
                        self._upsert_single_row(
                            conn,
                            table_ref,
                            dialect,
                            all_fields,
                            pk_fields,
                            non_pk_fields,
                            row,
                        )
                        written += 1
                    except Exception as exc:
                        logger.warning("UPSERT 行失败: pk=%s, error=%s", {k: row.get(k) for k in pk_fields}, exc)
                        skipped += 1
        except Exception as exc:
            raise SyncError(f"写入目标表失败: {exc}") from exc

        return written, skipped

    def _upsert_single_row(  # noqa: PLR0913 PLR0917
        self,
        conn: Any,
        table_ref: str,
        dialect: str,
        all_fields: list[str],
        pk_fields: list[str],
        non_pk_fields: list[str],
        row: dict[str, Any],
    ) -> None:
        """对单行执行 UPSERT（INSERT OR UPDATE）.

        各方言实现：
        - MySQL: INSERT ... ON DUPLICATE KEY UPDATE col=VALUES(col)
        - PostgreSQL: INSERT ... ON CONFLICT (pk) DO UPDATE SET col=EXCLUDED.col
        - SQLite: INSERT ... ON CONFLICT(pk) DO UPDATE SET col=excluded.col
        """
        if not pk_fields:
            # 无主键：直接 INSERT
            self._insert_only(conn, table_ref, dialect, all_fields, row)
            return

        if dialect == EngineType.MYSQL:
            self._upsert_mysql(conn, table_ref, all_fields, non_pk_fields, row)
        elif dialect == EngineType.POSTGRESQL:
            self._upsert_postgresql(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
        elif dialect == EngineType.SQLITE:
            self._upsert_sqlite(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
        else:
            self._insert_only(conn, table_ref, dialect, all_fields, row)

    def _upsert_mysql(
        self,
        conn: Any,
        table_ref: str,
        all_fields: list[str],
        non_pk_fields: list[str],
        row: dict[str, Any],
    ) -> None:
        """MySQL UPSERT: INSERT ... ON DUPLICATE KEY UPDATE."""
        col_refs = ", ".join(self._quote_ident(c, EngineType.MYSQL) for c in all_fields)
        placeholders = ", ".join(f":{c}" for c in all_fields)
        set_clause = ", ".join(
            f"{self._quote_ident(c, EngineType.MYSQL)}=VALUES({self._quote_ident(c, EngineType.MYSQL)})"
            for c in non_pk_fields
        )
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
        if set_clause:
            sql += f" ON DUPLICATE KEY UPDATE {set_clause}"
        params = {c: row.get(c) for c in all_fields}
        conn.execute(text(sql), params)

    def _upsert_postgresql(  # noqa: PLR0913 PLR0917
        self,
        conn: Any,
        table_ref: str,
        all_fields: list[str],
        pk_fields: list[str],
        non_pk_fields: list[str],
        row: dict[str, Any],
    ) -> None:
        """PostgreSQL UPSERT: INSERT ... ON CONFLICT (pk) DO UPDATE SET ..."""
        col_refs = ", ".join(self._quote_ident(c, EngineType.POSTGRESQL) for c in all_fields)
        placeholders = ", ".join(f":{c}" for c in all_fields)
        pk_refs = ", ".join(self._quote_ident(c, EngineType.POSTGRESQL) for c in pk_fields)
        set_clause = ", ".join(
            f"{self._quote_ident(c, EngineType.POSTGRESQL)}=EXCLUDED.{self._quote_ident(c, EngineType.POSTGRESQL)}"
            for c in non_pk_fields
        )
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) ON CONFLICT ({pk_refs}) DO UPDATE SET {set_clause}"
        params = {c: row.get(c) for c in all_fields}
        conn.execute(text(sql), params)

    def _upsert_sqlite(  # noqa: PLR0913 PLR0917
        self,
        conn: Any,
        table_ref: str,
        all_fields: list[str],
        pk_fields: list[str],
        non_pk_fields: list[str],
        row: dict[str, Any],
    ) -> None:
        """SQLite UPSERT: INSERT ... ON CONFLICT(pk) DO UPDATE SET ..."""
        col_refs = ", ".join(self._quote_ident(c, EngineType.SQLITE) for c in all_fields)
        placeholders = ", ".join(f":{c}" for c in all_fields)
        pk_refs = ", ".join(self._quote_ident(c, EngineType.SQLITE) for c in pk_fields)
        set_clause = ", ".join(
            f"{self._quote_ident(c, EngineType.SQLITE)}=excluded.{self._quote_ident(c, EngineType.SQLITE)}"
            for c in non_pk_fields
        )
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) ON CONFLICT({pk_refs}) DO UPDATE SET {set_clause}"
        params = {c: row.get(c) for c in all_fields}
        conn.execute(text(sql), params)

    def _insert_only(
        self,
        conn: Any,
        table_ref: str,
        dialect: str,
        all_fields: list[str],
        row: dict[str, Any],
    ) -> None:
        """纯 INSERT（无主键或不支持 UPSERT 的方言）."""
        col_refs = ", ".join(self._quote_ident(c, dialect) for c in all_fields)
        placeholders = ", ".join(f":{c}" for c in all_fields)
        sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
        params = {c: row.get(c) for c in all_fields}
        conn.execute(text(sql), params)

    # ================================================================
    # 日志辅助
    # ================================================================

    @staticmethod
    def _finalize_log(  # noqa: PLR0913 PLR0917
        log: SyncLog,
        status: str,
        rows_read: int,
        rows_written: int,
        started_at: datetime,
        message: str = "",
    ) -> None:
        elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
        log.status = status
        log.rows_read = rows_read
        log.rows_written = rows_written
        log.finished_at = timezone.now()
        log.duration_ms = elapsed_ms
        if message:
            log.error_message = message

    # ================================================================
    # 工具方法
    # ================================================================

    def _format_target_table_ref(self, dialect: str) -> str:
        """构造目标表引用."""
        config = self.config
        quote_char = "`" if dialect == EngineType.MYSQL else '"'
        schema = config.target_schema
        table = config.target_table
        if schema and dialect != EngineType.SQLITE:
            return f"{quote_char}{schema}{quote_char}.{quote_char}{table}{quote_char}"
        return f"{quote_char}{table}{quote_char}"

    @staticmethod
    def _quote_ident(name: str, dialect: str) -> str:
        """标识符引用."""
        if dialect == EngineType.MYSQL:
            return f"`{name}`"
        return f'"{name}"'


__all__ = ["BatchSyncResult", "SyncError", "SyncPreview", "SyncService"]
