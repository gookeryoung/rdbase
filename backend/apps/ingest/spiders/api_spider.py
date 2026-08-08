"""REST/JSON API 爬取 Spider.

从 REST/JSON API 端点爬取数据，使用 JSONPath 定位响应中的条目数组，
逐条 yield 为 dict 供 pipeline 处理。支持基于 JSONPath 的下一页 URL 翻页。

parse_config 结构::

    {
        "items_path": "$.data.items[*]",      // JSONPath 定位条目数组（省略则视响应为列表）
        "next_page_path": "$.pagination.next", // 可选，下一页 URL 的 JSONPath
        "next_page_max": 10                     // 可选，最大翻页数（默认 0=不限）
    }
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse  # type: ignore[import-not-found]
from scrapy.http import Request, Response  # type: ignore[import-not-found]

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
        """发起首个请求，附带已解密请求头."""
        method = str(self.request_config.get("method", "GET")).upper()
        body = self.request_config.get("body")
        for url in self.start_urls:
            yield Request(
                url,
                method=method,
                headers=self.headers or None,
                body=json.dumps(body) if body else None,
                callback=self.parse,
                dont_filter=True,
            )

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
