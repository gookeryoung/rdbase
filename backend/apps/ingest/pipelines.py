"""Scrapy Item Pipeline — 字段映射与批量写入目标数据源.

接收 spider 产出的原始 dict 条目，按字段映射（direct/constant）转换为目标行，
批量写入目标数据源的目标表。写入统计（rows_written/rows_skipped）写入
crawler.stats，供 :func:`apps.ingest.engine._run_spider` 读取。

字段映射逻辑与 sync_service 一致：
- direct：从源字段读取值写入目标字段
- constant：忽略源字段，使用 fixed_value

冲突策略（upsert/skip/error）由 :mod:`apps.ingest.writer` 实现。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.engine import Engine

from apps.datasources.engine import get_engine
from apps.datasources.models import DataSource
from apps.ingest.writer import write_rows

logger = logging.getLogger(__name__)

_STATS_WRITTEN = "ingest_rows_written"
_STATS_SKIPPED = "ingest_rows_skipped"


class FieldMappingPipeline:
    """字段映射与批量写入 pipeline.

    通过 :meth:`from_crawler` 创建，在 open_spider 时初始化目标引擎与字段配置，
    process_item 时应用映射并批量写入，close_spider 时刷新剩余批次并写统计。
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._mappings: list[dict[str, Any]] = []
        self._target_table: str = ""
        self._conflict_strategy: str = "upsert"
        self._batch_size: int = 500
        self._target_fields: list[str] = []
        self._pk_fields: list[str] = []
        self._batch: list[dict[str, Any]] = []
        self._written: int = 0
        self._skipped: int = 0
        self._stats: Any = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> FieldMappingPipeline:  # type: ignore[missing-override-decorator, override]
        """从 crawler 创建 pipeline，绑定 stats 收集器."""
        pipeline = cls()
        pipeline._stats = crawler.stats
        return pipeline

    def open_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]
        """初始化目标引擎与字段配置（无目标数据源时跳过）."""
        ds_id = getattr(spider, "target_datasource_id", None)
        if ds_id is None:
            return

        self._mappings = list(getattr(spider, "mappings", []) or [])
        if not self._mappings:
            logger.warning("爬取任务未配置字段映射，pipeline 不写入")
            return

        self._target_table = getattr(spider, "target_table", "")
        self._conflict_strategy = getattr(spider, "conflict_strategy", "upsert")
        self._batch_size = max(1, int(getattr(spider, "batch_size", 500)))
        self._target_fields = [str(m["target_field"]) for m in self._mappings]
        self._pk_fields = [str(m["target_field"]) for m in self._mappings if m.get("is_pk")]

        try:
            ds = DataSource.objects.get(pk=ds_id)
            self._engine = get_engine(ds)
        except DataSource.DoesNotExist:
            logger.error("目标数据源 %s 不存在，pipeline 无法写入", ds_id)

    def process_item(self, item: Any, spider: Any) -> Any:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """应用字段映射并加入批次，满批时写入."""
        if self._engine is None or not self._mappings:
            return item

        row = self._apply_mapping(item)
        self._batch.append(row)
        if len(self._batch) >= self._batch_size:
            self._flush()
        return item

    def close_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """刷新剩余批次并写入统计."""
        if self._engine is not None and self._batch:
            self._flush()
        if self._stats is not None:
            self._stats.set_value(_STATS_WRITTEN, self._written)
            self._stats.set_value(_STATS_SKIPPED, self._skipped)

    def _apply_mapping(self, item: Any) -> dict[str, Any]:
        """按字段映射将源条目转为目标行."""
        source: dict[str, Any] = dict(item) if isinstance(item, dict) else {}
        row: dict[str, Any] = {}
        for m in self._mappings:
            target_field = str(m["target_field"])
            if m.get("mapping_type") == "constant":
                row[target_field] = m.get("fixed_value", "")
            else:
                row[target_field] = source.get(str(m["source_field"]))
        return row

    def _flush(self) -> None:
        """将当前批次写入目标表并累加统计."""
        if not self._batch or self._engine is None:
            return
        written, skipped = write_rows(
            self._engine,
            self._batch,
            target_table=self._target_table,
            target_fields=self._target_fields,
            pk_fields=self._pk_fields,
            conflict_strategy=self._conflict_strategy,
        )
        self._written += written
        self._skipped += skipped
        self._batch.clear()


__all__ = ["FieldMappingPipeline"]
