"""Webhook 被动接收 pipeline 同步执行器（iter-54）.

WEBHOOK 源类型不经 Scrapy spider，由 ``POST /ingest/webhook/{token}`` 端点接收
外部推送的 payload 后，本模块同步驱动完整 pipeline 链：

    CleaningPipeline → ValidationPipeline → FieldMappingPipeline

与 :mod:`apps.ingest.engine` 的差异：

- 无 Scrapy CrawlerProcess，直接实例化 pipeline 并调用 ``open_spider`` /
  ``process_item`` / ``close_spider``。
- 用简单 stats 收集器（dict-like）替代 ``crawler.stats``。
- 用 :class:`_WebhookSpiderProxy` 提供 pipeline 所需的 spider 属性（task_id、
  clean_config、mappings 等），无需构造真实 spider。
- 每次接收产生一条 :class:`IngestLog` 记录，便于 :class:`ValidationPipeline`
  关联质量报告并更新 ``quality_score``。

清洗丢弃的 item（``DropItem``）计入 ``rows_skipped``，不中断后续 item 处理。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast

from django.utils import timezone

from apps.ingest.cleaning import CleaningPipeline, DropItem
from apps.ingest.models import IngestLog, IngestLogStatus, IngestTask
from apps.ingest.pipelines import FieldMappingPipeline
from apps.ingest.validation import ValidationPipeline

logger = logging.getLogger(__name__)


class _SimpleStats:
    """简单 stats 收集器，提供 Scrapy stats 接口子集.

    pipeline 通过 ``stats.get_value`` / ``set_value`` / ``inc_value`` 读写统计，
    本类用字典内存存储，无需 Scrapy 依赖。
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def get_value(self, key: str, default: Any = None) -> Any:
        """读取统计值，不存在时返回默认值."""
        return self._values.get(key, default)

    def set_value(self, key: str, value: Any) -> None:
        """设置统计值."""
        self._values[key] = value

    def inc_value(self, key: str, amount: int = 1) -> None:
        """累加统计值."""
        self._values[key] = self._values.get(key, 0) + amount


def _build_spider_proxy(task: IngestTask, mappings: list[dict[str, Any]]) -> SimpleNamespace:
    """构造 pipeline 所需的 spider 代理对象.

    pipeline 通过 ``getattr(spider, attr, default)`` 读取配置，本函数将 task
    的字段映射为 spider 属性，避免构造真实 spider。
    """
    return SimpleNamespace(
        task_id=task.pk,
        clean_config=cast(dict[str, Any], task.clean_config or {}),
        validation_config=cast(dict[str, Any], task.validation_config or {}),
        mappings=mappings,
        target_datasource_id=task.target_datasource_id,
        target_table=task.target_table,
        conflict_strategy=task.conflict_strategy,
        batch_size=task.batch_size,
    )


def run_webhook_pipelines(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
    """同步执行 webhook payload 的完整 pipeline 链.

    处理流程：

    1. 创建 IngestLog 记录本次接收（status=SUCCESS 初始）。
    2. 实例化三个 pipeline，绑定简单 stats 收集器。
    3. ``open_spider`` 初始化各 pipeline（读取配置、初始化去重追踪器等）。
    4. 逐条 ``process_item``：清洗 → 校验 → 字段映射。清洗丢弃的 item
       抛 ``DropItem``，捕获后计入 ``rows_skipped``，不中断后续 item。
    5. ``close_spider`` 刷新剩余批次、写质量报告、更新 IngestLog.quality_score。
    6. 按写入统计判定最终状态（SUCCESS / PARTIAL / FAILED）。

    Args:
        task: 爬取任务实例（source_type=WEBHOOK）。
        items: 外部推送的原始数据条目列表（每条为 dict）。

    Returns:
        IngestLog: 本次接收的日志记录（含统计与质量分）。
    """
    started_at = timezone.now()
    log = IngestLog.objects.create(
        task=task,
        status=IngestLogStatus.SUCCESS,
        rows_read=len(items),
        started_at=started_at,
    )

    mappings = [
        {
            "source_field": m.source_field,
            "target_field": m.target_field,
            "mapping_type": m.mapping_type,
            "fixed_value": m.fixed_value,
            "is_pk": m.is_pk,
        }
        for m in task.field_mappings.all()
    ]
    spider = _build_spider_proxy(task, mappings)
    stats = _SimpleStats()

    cleaning = CleaningPipeline()
    cleaning._stats = stats
    validation = ValidationPipeline()
    validation._stats = stats
    field_mapping = FieldMappingPipeline()
    field_mapping._stats = stats

    cleaning.open_spider(spider)
    validation.open_spider(spider)
    field_mapping.open_spider(spider)

    rows_skipped = 0
    try:
        for item in items:
            try:
                cleaned = cleaning.process_item(item, spider)
                validation.process_item(cleaned, spider)
                field_mapping.process_item(cleaned, spider)
            except DropItem:
                rows_skipped += 1
    finally:
        cleaning.close_spider(spider)
        validation.close_spider(spider)
        field_mapping.close_spider(spider)

    rows_written = int(stats.get_value("ingest_rows_written", 0) or 0)
    # 写入跳过数 = 清洗丢弃 + 写入冲突跳过
    rows_skipped += int(stats.get_value("ingest_rows_skipped", 0) or 0)
    quality_score = float(stats.get_value("ingest_quality_score", 100.0) or 100.0)

    log.rows_written = rows_written
    log.rows_skipped = rows_skipped
    log.quality_score = quality_score
    # 有跳过行（清洗丢弃或写入冲突）时为 PARTIAL，否则 SUCCESS
    if rows_skipped > 0:
        log.status = IngestLogStatus.PARTIAL
    else:
        log.status = IngestLogStatus.SUCCESS
    log.finished_at = timezone.now()
    log.duration_ms = int((log.finished_at - started_at).total_seconds() * 1000)
    log.save()

    # 更新任务的最近爬取时间（webhook 接收视为一次同步）
    task.last_run_at = log.finished_at
    task.last_sync_at = log.finished_at
    task.save(update_fields=["last_run_at", "last_sync_at"])

    return log


__all__ = ["run_webhook_pipelines"]
