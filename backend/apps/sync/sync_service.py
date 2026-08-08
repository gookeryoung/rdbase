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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import connections
from django.utils import timezone
from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.datasources.engine import get_engine as get_ds_engine
from apps.datasources.models import EngineType

from .models import (
    AlertLevel,
    ConflictStrategy,
    SyncAlert,
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
                    # 仅在重试全部耗尽的最终失败时告警一次，避免每次重试都产生告警。
                    SyncAlert.raise_alert(config, str(exc), level=AlertLevel.ERROR)

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
        max_workers: int = 1,
    ) -> BatchSyncResult:
        """批量执行同步.

        Args:
            config_ids: 同步配置 ID 列表。
            force_full: 强制全量同步。
            stop_on_error: 遇错即停（并发模式下为尽力而为：收到首个失败后取消
                尚未开始的任务，已在运行的任务仍会跑完）。
            max_workers: 并发线程数，默认 1（串行）。同步为 I/O 密集任务，
                适合线程池并发；每个线程独立持有 Django 数据库连接，任务结束后
                主动关闭以避免连接泄漏。

        Returns:
            BatchSyncResult: 批量同步结果。
        """
        result = BatchSyncResult()

        configs = SyncConfig.objects.filter(pk__in=config_ids, status=SyncStatus.ACTIVE)
        config_map = {c.pk: c for c in configs}

        # 过滤出可执行配置，其余计入 skipped（不存在或非 active）。
        # 对 config_ids 去重：同一 config 实例若被并发执行两次，会对同一 Python
        # 对象并发写 status/retry_count/last_sync_at 造成竞态，故按 ID 去重。
        # total 基于去重后的唯一 ID 数，保证 total == succeeded + failed + skipped。
        runnable: list[SyncConfig] = []
        seen: set[int] = set()
        for config_id in config_ids:
            if config_id in seen:
                continue
            seen.add(config_id)
            config = config_map.get(config_id)
            if config is None or not config.is_active:
                result.skipped += 1
                continue
            runnable.append(config)

        result.total = len(seen)

        if max_workers <= 1:
            SyncService._run_batch_serial(runnable, result, force_full=force_full, stop_on_error=stop_on_error)
        else:
            SyncService._run_batch_concurrent(
                runnable, result, force_full=force_full, stop_on_error=stop_on_error, max_workers=max_workers
            )

        return result

    @staticmethod
    def _run_batch_serial(
        configs: list[SyncConfig],
        result: BatchSyncResult,
        *,
        force_full: bool,
        stop_on_error: bool,
    ) -> None:
        """串行执行批量同步，遇错即停语义精确."""
        for config in configs:
            try:
                log = SyncService(config).run(force_full=force_full)
                result.succeeded += 1
                result.results.append(log)
            except SyncError:
                result.failed += 1
                if stop_on_error:
                    break

    @staticmethod
    def _run_batch_concurrent(
        configs: list[SyncConfig],
        result: BatchSyncResult,
        *,
        force_full: bool,
        stop_on_error: bool,
        max_workers: int,
    ) -> None:
        """线程池并发执行批量同步.

        同步任务以数据库 I/O 为主，线程池可在等待期间并发推进其它任务。
        每个 worker 运行结束后关闭本线程的 Django 连接，避免连接泄漏。
        stop_on_error 为尽力而为：收到首个失败后取消尚未开始的任务。
        """
        if not configs:
            return
        workers = min(max_workers, len(configs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(SyncService._run_one, config, force_full=force_full) for config in configs}
            stopped = False
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    if fut.cancelled():
                        continue
                    log = fut.result()
                    if log is None:
                        result.failed += 1
                        if stop_on_error and not stopped:
                            stopped = True
                            for p in pending:
                                p.cancel()
                    else:
                        result.succeeded += 1
                        result.results.append(log)

    @staticmethod
    def _run_one(config: SyncConfig, *, force_full: bool) -> SyncLog | None:
        """在独立线程中执行单个同步任务.

        返回同步日志；失败时返回 None（由调用方计入 failed）。
        任务结束后关闭本线程的 Django 数据库连接，防止线程池复用线程时连接泄漏。
        """
        try:
            return SyncService(config).run(force_full=force_full)
        except SyncError:
            return None
        finally:
            connections.close_all()

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

        # 按 source_db_alias 选择连接（connections[alias] 为线程局部，
        # 每个线程持有各自的连接，跨线程互不干扰）。
        try:
            conn = connections[db_alias]
        except Exception as exc:
            raise SyncError(f"无效的数据库别名（alias={db_alias}）: {exc}") from exc

        try:
            with conn.cursor() as _cursor:
                pass  # 验证连接可用
        except Exception as exc:
            raise SyncError(f"无法连接数据库（alias={db_alias}）: {exc}") from exc

        # 源方言由 Django 连接自身决定（conn.vendor），据此选择标识符引号与参数占位符，
        # 使源读取兼容 SQLite/MySQL/PostgreSQL 等后端，而非硬编码 SQLite。
        dialect = self._resolve_source_dialect(conn.vendor)

        # 构造 SQL
        table_ref = self._format_source_table_ref(dialect)
        sql = f"SELECT * FROM {table_ref}"
        params: dict[str, Any] = {}

        if mode == SyncMode.INCREMENTAL and config.last_sync_at and config.timestamp_field:
            ts_field = self._quote_ident(config.timestamp_field, dialect)
            placeholder = self._named_placeholder("last_sync", dialect)
            sql += f" WHERE {ts_field} > {placeholder}"
            params["last_sync"] = config.last_sync_at.isoformat()

        sql += f" ORDER BY {self._quote_ident('id', dialect)} ASC"

        try:
            with conn.cursor() as cursor:
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
        """批量写入目标表.

        依据 config.conflict_strategy 决定主键冲突处理方式：
        - UPSERT：冲突则更新（INSERT ... ON CONFLICT DO UPDATE）。
        - SKIP：冲突则跳过（INSERT ... ON CONFLICT DO NOTHING / INSERT IGNORE），
          计入 skipped，保留目标已有值。
        - ERROR：冲突则报错（普通 INSERT），任一行冲突即抛 SyncError 并回滚整批，
          不吞异常、不跳过。

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
        strategy = self.config.conflict_strategy

        # 批量写入
        written = 0
        skipped = 0

        try:
            with engine.begin() as conn:
                for row in rows:
                    if strategy == ConflictStrategy.ERROR:
                        # ERROR 策略：不捕获行级异常，任一行冲突即回滚整批并抛出。
                        self._write_single_row(
                            conn, table_ref, dialect, strategy, all_fields, pk_fields, non_pk_fields, row
                        )
                        written += 1
                        continue
                    try:
                        row_written = self._write_single_row(
                            conn, table_ref, dialect, strategy, all_fields, pk_fields, non_pk_fields, row
                        )
                        if row_written:
                            written += 1
                        else:
                            # SKIP 策略下主键冲突：目标已存在，保留原值并计入跳过。
                            skipped += 1
                    except Exception as exc:
                        logger.warning("写入行失败: pk=%s, error=%s", {k: row.get(k) for k in pk_fields}, exc)
                        skipped += 1
        except Exception as exc:
            raise SyncError(f"写入目标表失败: {exc}") from exc

        return written, skipped

    def _write_single_row(  # noqa: PLR0913 PLR0917
        self,
        conn: Any,
        table_ref: str,
        dialect: str,
        strategy: str,
        all_fields: list[str],
        pk_fields: list[str],
        non_pk_fields: list[str],
        row: dict[str, Any],
    ) -> bool:
        """按冲突策略写入单行.

        - 无主键：退化为纯 INSERT（无冲突判定依据）。
        - UPSERT：冲突则更新（各方言 ON CONFLICT DO UPDATE）。
        - SKIP：冲突则跳过（各方言 ON CONFLICT DO NOTHING）。
        - ERROR：纯 INSERT，冲突自然抛出数据库异常。

        Returns:
            bool: 是否实际写入（True 表示插入/更新，False 表示 SKIP 冲突未写入）。
        """
        if not pk_fields:
            # 无主键：无冲突判定依据，直接 INSERT
            self._insert_only(conn, table_ref, dialect, all_fields, row)
            return True

        if strategy == ConflictStrategy.ERROR:
            self._insert_only(conn, table_ref, dialect, all_fields, row)
            return True
        if strategy == ConflictStrategy.SKIP:
            return self._skip_single_row(conn, table_ref, dialect, all_fields, pk_fields, row)
        self._upsert_single_row(conn, table_ref, dialect, all_fields, pk_fields, non_pk_fields, row)
        return True

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
        if dialect == EngineType.MYSQL:
            self._upsert_mysql(conn, table_ref, all_fields, non_pk_fields, row)
        elif dialect == EngineType.POSTGRESQL:
            self._upsert_postgresql(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
        elif dialect == EngineType.SQLITE:
            self._upsert_sqlite(conn, table_ref, all_fields, pk_fields, non_pk_fields, row)
        else:
            self._insert_only(conn, table_ref, dialect, all_fields, row)

    def _skip_single_row(  # noqa: PLR0913 PLR0917
        self,
        conn: Any,
        table_ref: str,
        dialect: str,
        all_fields: list[str],
        pk_fields: list[str],
        row: dict[str, Any],
    ) -> bool:
        """对单行执行"冲突则跳过"写入（INSERT ... ON CONFLICT DO NOTHING）.

        各方言实现：
        - MySQL: INSERT IGNORE INTO ...
        - PostgreSQL: INSERT ... ON CONFLICT (pk) DO NOTHING
        - SQLite: INSERT ... ON CONFLICT(pk) DO NOTHING（或 INSERT OR IGNORE）
        - 其它方言：退化为纯 INSERT

        Returns:
            bool: 是否实际插入（True 插入成功，False 因主键冲突被跳过）。
            以受影响行数（rowcount）判定：冲突未写入时为 0。
        """
        col_refs = ", ".join(self._quote_ident(c, dialect) for c in all_fields)
        placeholders = ", ".join(f":{c}" for c in all_fields)
        params = {c: row.get(c) for c in all_fields}

        if dialect == EngineType.MYSQL:
            sql = f"INSERT IGNORE INTO {table_ref} ({col_refs}) VALUES ({placeholders})"
        elif dialect in (EngineType.POSTGRESQL, EngineType.SQLITE):
            pk_refs = ", ".join(self._quote_ident(c, dialect) for c in pk_fields)
            sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders}) ON CONFLICT ({pk_refs}) DO NOTHING"
        else:
            sql = f"INSERT INTO {table_ref} ({col_refs}) VALUES ({placeholders})"

        cursor = conn.execute(text(sql), params)
        # rowcount == 0 表示冲突被跳过（DO NOTHING / IGNORE 未写入任何行）。
        # 部分驱动可能返回 -1（未知），此时按已写入处理。
        return cursor.rowcount != 0

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

    # ================================================================
    # 源方言辅助
    # ================================================================

    # Django connection.vendor 到本模块方言标识（EngineType 值）的映射。
    # Django 的 vendor 取值为 sqlite/mysql/postgresql/oracle/microsoft，
    # 前三者与 EngineType 完全一致，其余方言暂回退为 PostgreSQL 风格（双引号）。
    _VENDOR_DIALECT_MAP: dict[str, str] = {
        "sqlite": EngineType.SQLITE,
        "mysql": EngineType.MYSQL,
        "postgresql": EngineType.POSTGRESQL,
    }

    @classmethod
    def _resolve_source_dialect(cls, vendor: str) -> str:
        """将 Django connection.vendor 解析为本模块方言标识.

        未知 vendor（如 oracle/microsoft）回退为 PostgreSQL 风格，
        使用标准 SQL 双引号标识符，避免因方言未知直接报错。
        """
        dialect = cls._VENDOR_DIALECT_MAP.get(vendor)
        if dialect is None:
            logger.warning("未知的源数据库 vendor=%s，回退为 PostgreSQL 标识符风格", vendor)
            return EngineType.POSTGRESQL
        return dialect

    @staticmethod
    def _named_placeholder(name: str, dialect: str) -> str:
        """按源方言构造命名参数占位符.

        Django DB-API 游标的 paramstyle 因后端而异：
        - sqlite3：named（:name），不支持 pyformat。
        - MySQLdb / psycopg：pyformat（%(name)s）。
        统一使用 dict 传参，占位符按方言切换以保证跨方言可用。
        """
        if dialect == EngineType.SQLITE:
            return f":{name}"
        return f"%({name})s"

    def _format_source_table_ref(self, dialect: str) -> str:
        """构造源表引用（支持 source_schema，方言化引号）."""
        config = self.config
        schema = config.source_schema
        table = config.source_table
        if schema and dialect != EngineType.SQLITE:
            return f"{self._quote_ident(schema, dialect)}.{self._quote_ident(table, dialect)}"
        return self._quote_ident(table, dialect)


__all__ = ["BatchSyncResult", "SyncError", "SyncPreview", "SyncService"]
