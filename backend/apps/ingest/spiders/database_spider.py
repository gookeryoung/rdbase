"""数据库直连爬取 Spider（P8-Q4）.

从指定 DataSource 直连数据库执行 SQL 查询，将结果行逐条 yield 为 dict，
走完整 Pipeline（Cleaning → Validation → FieldMapping → Writer）写入目标表。

不经 HTTP 下载器，直接在 ``start`` 中查询数据库并 yield 行。source_url 用
``datasource://{id}`` 形式引用源数据源 ID，SQL 与参数从 parse_config 取。

parse_config 结构::

    {
        "sql": "SELECT id, name, updated_at FROM users WHERE id > :min_id",
        "params": {"min_id": 0}            // 可选，参数化查询绑定参数
    }

增量策略 DB_TIMESTAMP（incremental_config）::

    {
        "strategy": "db_timestamp",
        "timestamp_field": "updated_at",   // 必填，WHERE 过滤的时间字段名
        "param_name": "last_sync_at"       // 可选，SQL 参数名（默认 last_sync_at）
    }

启用增量时自动将 ``task.last_sync_at`` 注入 SQL 参数。SQL 必须包含对应占位符
（如 ``WHERE updated_at > :last_sync_at``）；若未包含占位符则忽略增量过滤并记日志。

安全：SQL 仅来自任务配置（管理员维护），不接受外部输入；参数化查询绑定值，
避免字符串拼接导致的 SQL 注入。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.datasources.engine import get_engine
from apps.datasources.models import DataSource
from apps.ingest.models import IncrementalStrategy
from apps.ingest.spiders.base import BaseIngestSpider

logger = logging.getLogger(__name__)


class DatabaseIngestSpider(BaseIngestSpider):
    """数据库直连爬取 Spider.

    不发 HTTP 请求，直接在 ``start`` 方法中查询源 DataSource 并 yield 行。
    支持参数化 SQL（防注入）与基于 ``timestamp_field`` 的增量过滤。
    """

    name = "ingest_database"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # DATABASE 源不发 HTTP 请求，强制清空 start_urls 防止基类默认行为
        self.start_urls: list[str] = []

    # --------------------------------------------------------------
    # 主入口：直接 yield 行（不经 Scrapy 下载器）
    # --------------------------------------------------------------

    def start(self) -> Iterator[Any]:  # type: ignore[missing-override-decorator, override]
        """查询源数据库并逐行 yield.

        Scrapy 2.10+ 的 ``start`` 方法支持直接 yield item，无需 Request/Response。
        """
        engine = self._get_source_engine()
        if engine is None:
            return

        sql = self._build_sql()
        if not sql:
            return

        params = self._build_params()

        try:
            with engine.connect() as conn:
                # 流式获取（yield_per 按批拉取，避免一次性加载全表到内存）
                result = conn.execution_options(stream_results=True).execute(text(sql), params or {})
                for row in result:
                    # Row 对象通过 _mapping 获取列名 -> 值字典
                    yield dict(row._mapping)
        except Exception as exc:
            logger.error("数据库查询失败: task_id=%s, sql=%s, error=%s", self.task_id, sql, exc)
            return

    # --------------------------------------------------------------
    # 辅助
    # --------------------------------------------------------------

    def _get_source_engine(self) -> Engine | None:
        """从 source_url 解析源数据源 ID 并返回 SQLAlchemy 引擎.

        source_url 格式：``datasource://{id}``。无效格式或数据源不存在返回 None。
        """
        ds_id = self._parse_datasource_id(self.source_url)
        if ds_id is None:
            logger.error("DATABASE 源 source_url 格式错误，期望 datasource://{id}: %s", self.source_url)
            return None
        try:
            ds = DataSource.objects.get(pk=ds_id)
        except DataSource.DoesNotExist:
            logger.error("DATABASE 源数据源不存在: id=%s", ds_id)
            return None
        if not ds.is_active:
            logger.warning("DATABASE 源数据源未启用: id=%s", ds_id)
            return None
        return get_engine(ds)

    @staticmethod
    def _parse_datasource_id(source_url: str) -> int | None:
        """从 ``datasource://{id}`` URL 解析数据源 ID.

        支持两种写法：

        - ``datasource://{id}``：urlparse 将 ``{id}`` 放入 netloc
        - ``datasource:///{id}``：urlparse 将 ``{id}`` 放入 path
        """
        if not source_url:
            return None
        try:
            parsed = urlparse(source_url)
        except ValueError:
            return None
        if parsed.scheme != "datasource":
            return None
        # urlparse("datasource://42") -> netloc="42", path=""
        # urlparse("datasource:///42") -> netloc="", path="/42"
        id_str = parsed.path.lstrip("/") or parsed.netloc
        if not id_str:
            return None
        try:
            return int(id_str)
        except ValueError:
            return None

    def _build_sql(self) -> str:
        """从 parse_config 取 SQL 文本（必填）."""
        sql = self.parse_config.get("sql")
        if not sql or not isinstance(sql, str):
            logger.error("DATABASE 源 parse_config.sql 未配置或非字符串: task_id=%s", self.task_id)
            return ""
        return sql

    def _build_params(self) -> dict[str, Any]:
        """构造 SQL 绑定参数.

        合并来源：
        1. ``parse_config.params``（用户配置的固定参数）
        2. 增量策略注入的 ``last_sync_at``（仅 DB_TIMESTAMP 且 SQL 含占位符时）
        """
        params: dict[str, Any] = {}
        cfg_params = self.parse_config.get("params")
        if isinstance(cfg_params, dict):
            params.update({str(k): v for k, v in cfg_params.items()})

        # 增量策略：DB_TIMESTAMP 注入 last_sync_at
        strategy = str(self.incremental_config.get("strategy", IncrementalStrategy.NONE))
        if strategy == IncrementalStrategy.DB_TIMESTAMP:
            self._inject_incremental_param(params)

        return params

    def _inject_incremental_param(self, params: dict[str, Any]) -> None:
        """按 DB_TIMESTAMP 策略注入 last_sync_at 参数.

        参数名取 ``incremental_config.param_name``（默认 ``last_sync_at``）。
        若 SQL 不含该占位符则跳过注入并记日志（容错：用户可能配置了 strategy
        但 SQL 未带对应占位符，避免参数错配导致查询失败）。

        首次执行（``last_sync_at`` 为空）时注入极早时间戳（``1970-01-01T00:00:00``）
        实现全量拉取：SQL 占位符有值绑定不会报错，且 ``updated_at > 1970`` 匹配全部行。

        Args:
            params: 待注入的参数字典（in-place 修改）。
        """
        cfg = self.incremental_config or {}
        param_name = str(cfg.get("param_name", "last_sync_at"))
        sql = self._build_sql()
        # 简单检查 SQL 是否含 :param_name 占位符
        if f":{param_name}" not in sql:
            logger.warning(
                "DB_TIMESTAMP 增量策略启用但 SQL 不含 :%s 占位符，跳过增量过滤: task_id=%s",
                param_name,
                self.task_id,
            )
            return
        # last_sync_at 由 engine._apply_task_status 写入 task.last_sync_at
        # 传入 spider 时通过 spider_kwargs 注入（见 _build_spider_kwargs）
        last_sync = self.request_config.get("__last_sync_at__")
        if last_sync is None:
            logger.info("DB_TIMESTAMP 增量策略启用但 last_sync_at 为空，首次全量拉取: task_id=%s", self.task_id)
            # 注入极早时间戳使 WHERE 条件匹配全部行（全量拉取）
            last_sync = "1970-01-01T00:00:00"
        params[param_name] = last_sync


__all__ = ["DatabaseIngestSpider"]
