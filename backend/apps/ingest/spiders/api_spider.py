"""REST/JSON API 爬取 Spider.

从 REST/JSON API 端点爬取数据，使用 JSONPath 定位响应中的条目数组，
逐条 yield 为 dict 供 pipeline 处理。支持基于 JSONPath 的下一页 URL 翻页。

parse_config 结构::

    {
        "items_path": "$.data.items[*]",      // JSONPath 定位条目数组（省略则视响应为列表）
        "next_page_path": "$.pagination.next", // 可选，下一页 URL 的 JSONPath
        "next_page_max": 10                     // 可选，最大翻页数（默认 0=不限）
    }

增量策略 API_UPDATED_AT（incremental_config）::

    {
        "strategy": "api_updated_at",
        "param_name": "updated_since",   // 可选，查询参数名（默认 updated_since）
        "format": "iso"                  // 可选，时间格式（"iso" 或 strftime 如 "%Y-%m-%d"）
    }

启用增量时自动将 ``task.last_sync_at`` 作为查询参数追加到 start_url。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from jsonpath_ng.ext import parse as jsonpath_parse  # type: ignore[import-not-found]
from scrapy.http import Request, Response  # type: ignore[import-not-found]

from apps.ingest.models import IncrementalStrategy
from apps.ingest.spiders.base import BaseIngestSpider

logger = logging.getLogger(__name__)


class ApiIngestSpider(BaseIngestSpider):
    """REST/JSON API 爬取 Spider.

    从 source_url 发起 GET 请求，解析 JSON 响应，用 JSONPath 提取条目数组，
    逐条 yield 为 dict。支持基于 next_page_path 的自动翻页。
    """

    name = "ingest_api"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.source_url:
            self.start_urls = [self.source_url]

    def start_requests(self) -> Iterator[Request]:  # type: ignore[missing-override-decorator, override]
        """发起首个请求，附带已解密请求头与增量参数."""
        method = str(self.request_config.get("method", "GET")).upper()
        body = self.request_config.get("body")

        # 增量策略：API_UPDATED_AT 注入查询参数
        strategy = str(self.incremental_config.get("strategy", IncrementalStrategy.NONE))
        urls = self.start_urls
        if strategy == IncrementalStrategy.API_UPDATED_AT:
            urls = [self._inject_updated_param(url) for url in urls]

        for url in urls:
            yield Request(
                url,
                method=method,
                headers=self.headers or None,
                body=json.dumps(body) if body else None,
                callback=self.parse,
                dont_filter=True,
            )

    def _inject_updated_param(self, url: str) -> str:
        """按 API_UPDATED_AT 策略将 last_sync_at 作为查询参数追加到 URL.

        参数名取 ``incremental_config.param_name``（默认 ``updated_since``）。
        时间格式取 ``incremental_config.format``（默认 ``iso``，即 ISO 8601）；
        也支持 strftime 模式（如 ``%Y-%m-%d``）。

        首次执行（last_sync_at 为空）时不追加参数，全量拉取。
        """
        last_sync = self.request_config.get("__last_sync_at__")
        if not last_sync:
            logger.info("API_UPDATED_AT 增量策略启用但 last_sync_at 为空，首次全量拉取: task_id=%s", self.task_id)
            return url

        cfg = self.incremental_config or {}
        param_name = str(cfg.get("param_name", "updated_since"))
        fmt = str(cfg.get("format", "iso"))
        value = self._format_last_sync(last_sync, fmt)

        # 用 urlencode 正确拼接查询参数，避免手动拼接导致的转义问题
        parsed = urlparse(url)
        existing_params = dict(parse_qsl(parsed.query))
        existing_params[param_name] = value
        new_query = urlencode(existing_params)
        return (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
            if parsed.scheme
            else f"{url}&{param_name}={value}"
            if "?" in url
            else f"{url}?{param_name}={value}"
        )

    @staticmethod
    def _format_last_sync(last_sync: str, fmt: str) -> str:
        """按配置格式化 last_sync_at 时间字符串.

        Args:
            last_sync: ISO 8601 格式的时间字符串（由 engine 注入）。
            fmt: 格式标识，``"iso"`` 原样返回；其他值视为 strftime 模式。
        """
        if fmt == "iso":
            return last_sync
        try:
            dt = datetime.fromisoformat(last_sync)
            return dt.strftime(fmt)
        except (ValueError, TypeError):
            logger.warning("last_sync_at 格式化失败，回退 ISO: %s", last_sync)
            return last_sync

    def parse(self, response: Response, **kwargs: Any) -> Iterator[Any]:  # type: ignore[missing-override-decorator, override]
        """解析 JSON 响应，提取条目并翻页.

        Args:
            response: Scrapy 下载器返回的响应对象。
            kwargs: 回调参数（含 page 当前页码）。
        """
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("响应非合法 JSON: %s", exc)
            return

        yield from self._extract_items(data)

        yield from self._follow_next_page(data, kwargs.get("page", 1))

    def _extract_items(self, data: Any) -> Iterator[dict[str, Any]]:
        """用 JSONPath 从响应数据中提取条目数组.

        无 items_path 时：响应本身为列表则逐条 yield，否则视单对象为一条。
        """
        items_path = self.parse_config.get("items_path")
        if not items_path:
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
            elif isinstance(data, dict):
                yield data
            return

        try:
            expr = jsonpath_parse(str(items_path))
        except Exception as exc:  # jsonpath_ng 解析异常
            logger.error("JSONPath 解析失败: %s, error: %s", items_path, exc)
            return

        for match in expr.find(data):
            value = match.value
            if isinstance(value, dict):
                yield value
            elif isinstance(value, list):
                for sub in value:
                    if isinstance(sub, dict):
                        yield sub

    def _follow_next_page(self, data: Any, current_page: int) -> Iterator[Request]:
        """按 next_page_path 提取下一页 URL 并发起请求."""
        next_page_path = self.parse_config.get("next_page_path")
        if not next_page_path:
            return
        max_pages = int(self.parse_config.get("next_page_max", 0) or 0)
        if max_pages > 0 and current_page >= max_pages:
            return

        try:
            expr = jsonpath_parse(str(next_page_path))
        except Exception as exc:  # pragma: no cover - JSONPath 已在 items_path 验证
            logger.error("next_page JSONPath 解析失败: %s, error: %s", next_page_path, exc)
            return

        matches = expr.find(data)
        if not matches:
            return
        next_url = matches[0].value
        if not next_url or not isinstance(next_url, str):
            return

        method = str(self.request_config.get("method", "GET")).upper()
        body = self.request_config.get("body")
        yield Request(
            next_url,
            method=method,
            headers=self.headers or None,
            body=json.dumps(body) if body else None,
            callback=self.parse,
            cb_kwargs={"page": current_page + 1},
            dont_filter=True,
        )


__all__ = ["ApiIngestSpider"]
