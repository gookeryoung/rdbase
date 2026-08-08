"""Scrapy 爬取引擎编排.

提供两层入口：
- :func:`spawn_ingest`：Django 侧（web/调度）以子进程启动 ``run_ingest`` 命令，
  隔离 Twisted reactor，避免与 Django ASGI 共存冲突。
- :func:`execute_task`：子进程内（``run_ingest`` 命令调用）按 source_type 分派 spider，
  用 :class:`scrapy.crawler.CrawlerProcess` 运行 Scrapy，收集统计并写 IngestLog。

FieldMappingPipeline 在 Scrapy 内完成字段映射与目标表写入，统计通过
crawler.stats 回传（ingest_rows_written/ingest_rows_skipped）。

失败达最大重试时通过 :class:`IngestAlert` 产生告警。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from django.utils import timezone

from apps.ingest.models import (
    IngestAlert,
    IngestLog,
    IngestLogStatus,
    IngestTask,
    SourceType,
)
from apps.ingest.spiders.api_spider import ApiIngestSpider
from apps.ingest.spiders.base import BaseIngestSpider
from apps.ingest.spiders.file_spider import FileIngestSpider
from apps.ingest.spiders.html_spider import HtmlIngestSpider
from apps.ingest.spiders.rss_spider import RssIngestSpider
from apps.system.circuit_breaker import CircuitOpenError, get_breaker

logger = logging.getLogger(__name__)


class IngestError(ValueError):
    """爬取执行错误."""


@dataclass(frozen=True)
class SpiderStats:
    """单次爬取的统计结果."""

    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0


def spawn_ingest(task_id: int, *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """以子进程启动 ``run_ingest`` 命令执行爬取任务.

    Twisted reactor 与 Django ASGI 不兼容，故 Scrapy 必须在独立进程运行。
    本函数供 web/调度侧调用，禁用 ``shell=True``，list 形参避免命令注入。

    Args:
        task_id: 爬取任务 ID。
        timeout: 子进程超时秒数，None 表示不超时。

    Returns:
        subprocess.CompletedProcess: 子进程执行结果，调用方按 returncode 判断成败。
    """
    # manage.py 位于 backend/ 目录（engine.py 上溯三级）
    backend_dir = Path(__file__).resolve().parent.parent.parent
    cmd: list[str] = [sys.executable, "manage.py", "run_ingest", str(task_id)]
    logger.info("启动爬取子进程: task_id=%s", task_id)
    return subprocess.run(  # 显式 list 形参，无 shell，无命令注入风险
        cmd,
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def execute_task(task: IngestTask) -> IngestLog:
    """子进程内执行爬取任务（由 ``run_ingest`` 命令调用）.

    按 source_type 分派 spider，运行 Scrapy CrawlerProcess，收集统计并写日志。
    失败时累加任务 retry_count，达 max_retries 产生告警。

    调用前后驱动熔断器（``ingest:task:{id}``）：调用前判断是否放行，成功重置
    失败计数，失败累加。熔断器 OPEN 时直接记为失败日志，不启动 Scrapy 子进程。
    多 worker 部署时熔断状态经 Redis 共享，单 worker 降级为本地内存。

    PARTIAL 判定：rows_skipped > 0 时视为部分成功。

    Args:
        task: 爬取任务配置实例。

    Returns:
        IngestLog: 本次执行的日志记录。
    """
    started_at = timezone.now()
    log = IngestLog(task=task, status=IngestLogStatus.SUCCESS, started_at=started_at)
    breaker = get_breaker(f"ingest:task:{task.pk}")

    try:
        breaker.before_call()
    except CircuitOpenError as exc:
        # 熔断打开：不启动 Scrapy，直接记失败日志。
        log.status = IngestLogStatus.FAILED
        log.error_message = f"熔断器打开，跳过爬取: {exc}"
        log.finished_at = timezone.now()
        log.duration_ms = int((log.finished_at - started_at).total_seconds() * 1000)
        log.save()
        _apply_task_status(task, log)
        logger.warning("爬取任务 %s 被熔断器拒绝", task.name)
        return log

    try:
        stats = _run_spider(task)
        log.rows_read = stats.rows_read
        log.rows_written = stats.rows_written
        log.rows_skipped = stats.rows_skipped
        if stats.rows_skipped > 0 and stats.rows_written > 0:
            log.status = IngestLogStatus.PARTIAL
        else:
            log.status = IngestLogStatus.SUCCESS
        breaker.on_success()
    except IngestError as exc:
        log.status = IngestLogStatus.FAILED
        log.error_message = str(exc)
        logger.warning("爬取任务 %s 执行失败: %s", task.name, exc)
        breaker.on_failure()
        if task.retry_count + 1 >= task.max_retries:
            IngestAlert.raise_alert(task, f"爬取失败（已达最大重试 {task.max_retries} 次）: {exc}")
    finally:
        log.finished_at = timezone.now()
        log.duration_ms = int((log.finished_at - started_at).total_seconds() * 1000)
        log.save()
        _apply_task_status(task, log)

    return log


def _apply_task_status(task: IngestTask, log: IngestLog) -> None:
    """根据执行日志更新任务的运行时间与重试计数."""
    task.last_run_at = log.finished_at
    if log.status in (IngestLogStatus.SUCCESS, IngestLogStatus.PARTIAL):
        task.last_sync_at = log.finished_at
        task.retry_count = 0
    else:
        task.retry_count = min(task.retry_count + 1, task.max_retries)
    task.save(update_fields=["last_run_at", "last_sync_at", "retry_count"])


def _run_spider(task: IngestTask) -> SpiderStats:
    """按 source_type 分派 spider 并运行 Scrapy，返回统计.

    从 task 提取完整配置（含字段映射）注入 spider 与 pipeline，
    pipeline 通过 spider 属性读取写入配置。

    Raises:
        IngestError: Scrapy 启动或运行过程中发生预期异常时包装抛出。
    """
    from scrapy.crawler import CrawlerProcess  # type: ignore[import-not-found]

    spider_cls = _resolve_spider(task.source_type)
    settings = _build_scrapy_settings(task)
    spider_kwargs = _build_spider_kwargs(task)

    process = CrawlerProcess(settings, install_root_handler=False)
    crawler = process.create_crawler(spider_cls)
    process.crawl(crawler, **spider_kwargs)
    try:
        process.start()
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
        raise IngestError(f"Scrapy 运行失败: {exc}") from exc

    stats = crawler.stats
    rows_read = int(stats.get_value("item_scraped_count", 0) or 0)
    rows_written = int(stats.get_value("ingest_rows_written", 0) or 0)
    rows_skipped = int(stats.get_value("ingest_rows_skipped", 0) or 0)
    return SpiderStats(rows_read=rows_read, rows_written=rows_written, rows_skipped=rows_skipped)


def _resolve_spider(source_type: str) -> type[BaseIngestSpider]:
    """按源类型解析 Spider 类."""
    if source_type == SourceType.API:
        return ApiIngestSpider
    if source_type == SourceType.HTML:
        return HtmlIngestSpider
    if source_type == SourceType.FILE:
        return FileIngestSpider
    if source_type == SourceType.RSS:
        return RssIngestSpider
    if source_type in dict(SourceType.choices):
        logger.warning("源类型 %s 的专用 spider 尚未实现，使用 BaseIngestSpider 占位", source_type)
        return BaseIngestSpider
    raise IngestError(f"不支持的源类型: {source_type!r}")


def _build_spider_kwargs(task: IngestTask) -> dict[str, Any]:
    """从 IngestTask 构造 spider 初始化参数（含字段映射与写入配置）."""
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
    return {
        "source_url": task.source_url,
        "parse_config": cast(dict[str, Any], task.parse_config or {}),
        "headers": task.get_headers(),
        "request_config": cast(dict[str, Any], task.request_config or {}),
        "mappings": mappings,
        "target_datasource_id": task.target_datasource_id,
        "target_table": task.target_table,
        "conflict_strategy": task.conflict_strategy,
        "batch_size": task.batch_size,
    }


def _build_scrapy_settings(task: IngestTask) -> dict[str, Any]:
    """按任务配置构造 Scrapy settings 字典（含 pipeline 注册）."""
    request_config = task.request_config or {}
    return {
        "LOG_ENABLED": False,
        "ROBOTSTXT_OBEY": bool(task.obey_robots),
        "CONCURRENT_REQUESTS": int(request_config.get("concurrent_requests", 8)),
        "DOWNLOAD_TIMEOUT": int(request_config.get("timeout", 30)),
        "DOWNLOAD_DELAY": float(request_config.get("download_delay", 0.0)),
        "USER_AGENT": request_config.get("user_agent", "rdbase-ingest/1.0"),
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": bool(request_config.get("cookies_enabled", False)),
        "ITEM_PIPELINES": {"apps.ingest.pipelines.FieldMappingPipeline": 300},
    }


__all__ = [
    "IngestError",
    "SpiderStats",
    "execute_task",
    "spawn_ingest",
]
