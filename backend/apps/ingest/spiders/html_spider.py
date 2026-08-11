"""网页 HTML 爬取 Spider.

从 HTML 页面爬取结构化数据，使用 selectolax（CSS）或 lxml（XPath）定位行容器，
按字段配置提取文本或属性值。支持基于 CSS 选择器的下一页链接翻页。

parse_config 结构::

    {
        "container_selector": "table tbody tr",   // 行容器选择器
        "selector_type": "css",                    // "css"（默认）或 "xpath"
        "fields": {                                // 字段名 -> 选择器或 {selector, attr}
            "id": "td:nth-child(1)",
            "name": "td:nth-child(2)",
            "link": {"selector": "a", "attr": "href"}
        },
        "next_page_selector": "a.next-page",       // 可选，下一页链接 CSS 选择器
        "next_page_attr": "href",                   // 可选，链接属性名（默认 href）
        "next_page_max": 10                         // 可选，最大翻页数
    }

增量策略 HTML_FINGERPRINT（incremental_config）::

    {
        "strategy": "html_fingerprint"
    }

启用增量时计算首页 HTML 的 SHA-256 指纹，与上次存储的指纹比对：
- 指纹一致：页面未变化，跳过本次爬取（不产出 item，不翻页）。
- 指纹不一致：正常爬取，新指纹经 ``crawler.stats`` 回传给 engine 持久化到
  ``task.incremental_config._last_fingerprint``。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html  # type: ignore[import-not-found]
from scrapy.http import Request, Response  # type: ignore[import-not-found]
from selectolax.parser import HTMLParser  # type: ignore[import-not-found]

from apps.ingest.models import IncrementalStrategy
from apps.ingest.spiders.base import BaseIngestSpider

logger = logging.getLogger(__name__)

# crawler.stats 中存储 HTML 指纹的 key（engine 读取后写回 task.incremental_config）
_STAT_HTML_FINGERPRINT = "_html_fingerprint"


class HtmlIngestSpider(BaseIngestSpider):
    """网页 HTML 爬取 Spider.

    从 source_url 下载 HTML，按 container_selector 定位行容器，
    逐行提取字段值并 yield 为 dict。支持 CSS 与 XPath 两种选择器。
    """

    name = "ingest_html"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.source_url:
            self.start_urls = [self.source_url]

    def start_requests(self) -> Iterator[Request]:  # type: ignore[missing-override-decorator, override]
        """发起首个请求，附带已解密请求头."""
        for url in self.start_urls:
            yield Request(url, headers=self.headers or None, callback=self.parse, dont_filter=True)

    def parse(self, response: Response, **kwargs: Any) -> Iterator[Any]:  # type: ignore[missing-override-decorator, override]
        """解析 HTML 响应，提取行并翻页.

        增量策略 HTML_FINGERPRINT 仅检查首页（page=1）内容指纹；翻页请求不检查
        指纹（避免多页场景下后续页变化被忽略）。

        Args:
            response: Scrapy 下载器返回的响应对象。
            kwargs: 回调参数（含 page 当前页码）。
        """
        body = response.text
        selector_type = str(self.parse_config.get("selector_type", "css")).lower()

        # 增量策略：HTML_FINGERPRINT 检查首页指纹
        page = kwargs.get("page", 1)
        strategy = str(self.incremental_config.get("strategy", IncrementalStrategy.NONE))
        if strategy == IncrementalStrategy.HTML_FINGERPRINT and page <= 1:
            fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
            stored = str(self.incremental_config.get("_last_fingerprint", ""))
            if stored and fingerprint == stored:
                logger.info("HTML_FINGERPRINT 命中，首页未变化，跳过爬取: task_id=%s", self.task_id)
                return
            # 记录新指纹到 stats，engine 读取后写回 task.incremental_config
            if self.crawler is not None:
                self.crawler.stats.set_value(_STAT_HTML_FINGERPRINT, fingerprint)

        yield from self._extract_rows(body, selector_type)

        yield from self._follow_next_page(response, body, selector_type, kwargs.get("page", 1))

    def _extract_rows(self, body: str, selector_type: str) -> Iterator[dict[str, Any]]:
        """按 container_selector 提取行容器，逐行提取字段."""
        container = self.parse_config.get("container_selector")
        if not container:
            logger.warning("未配置 container_selector，HTML spider 不产出条目")
            return

        fields_config = self.parse_config.get("fields", {})
        if not fields_config:
            logger.warning("未配置 fields，HTML spider 不产出条目")
            return

        if selector_type == "xpath":
            yield from self._extract_rows_xpath(body, str(container), fields_config)
        else:
            yield from self._extract_rows_css(body, str(container), fields_config)

    def _extract_rows_css(self, body: str, container: str, fields_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """用 selectolax CSS 选择器提取行."""
        tree = HTMLParser(body)
        for node in tree.css(container):
            row: dict[str, Any] = {}
            for field_name, field_spec in fields_config.items():
                row[field_name] = self._extract_field_css(node, field_spec)
            yield row

    def _extract_rows_xpath(self, body: str, container: str, fields_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """用 lxml XPath 提取行."""
        tree = lxml_html.fromstring(body)
        for node in tree.xpath(container):
            row: dict[str, Any] = {}
            for field_name, field_spec in fields_config.items():
                row[field_name] = self._extract_field_xpath(node, field_spec)
            yield row

    @staticmethod
    def _extract_field_css(parent_node: Any, field_spec: Any) -> str:
        """从 CSS 节点提取字段值（文本或属性）."""
        if isinstance(field_spec, dict):
            selector = str(field_spec.get("selector", ""))
            attr = str(field_spec.get("attr", "text"))
        else:
            selector = str(field_spec)
            attr = "text"

        if not selector:
            return ""
        child = parent_node.css_first(selector)
        if child is None:
            return ""
        if attr == "text":
            return child.text(strip=True)
        if attr == "html":
            return child.html
        return str(child.attributes.get(attr, "") or "")

    @staticmethod
    def _extract_field_xpath(parent_node: Any, field_spec: Any) -> str:
        """从 XPath 节点提取字段值（文本或属性）."""
        if isinstance(field_spec, dict):
            selector = str(field_spec.get("selector", ""))
            attr = str(field_spec.get("attr", "text"))
        else:
            selector = str(field_spec)
            attr = "text"

        if not selector:
            return ""
        results = parent_node.xpath(selector)
        if not results:
            return ""
        first = results[0]
        if attr == "text":
            return str(first).strip() if hasattr(first, "strip") else str(getattr(first, "text", "")).strip()
        if attr == "html":
            return str(getattr(first, "html", ""))
        getter = getattr(first, "get", None)
        if getter is not None:
            return str(getter(attr) or "")
        return ""

    def _follow_next_page(
        self,
        response: Response,
        body: str,
        selector_type: str,
        current_page: int,
    ) -> Iterator[Request]:
        """按 next_page_selector 提取下一页链接并请求."""
        next_selector = self.parse_config.get("next_page_selector")
        if not next_selector:
            return
        max_pages = int(self.parse_config.get("next_page_max", 0) or 0)
        if max_pages > 0 and current_page >= max_pages:
            return

        next_attr = str(self.parse_config.get("next_page_attr", "href"))
        next_url = self._extract_next_url(body, str(next_selector), next_attr, selector_type)
        if not next_url:
            return
        # 相对 URL 转绝对
        next_url = urljoin(response.url, next_url)

        yield Request(
            next_url,
            headers=self.headers or None,
            callback=self.parse,
            cb_kwargs={"page": current_page + 1},
            dont_filter=True,
        )

    @staticmethod
    def _extract_next_url(body: str, selector: str, attr: str, selector_type: str) -> str:
        """从 HTML 中提取下一页 URL."""
        if selector_type == "xpath":
            tree = lxml_html.fromstring(body)
            results = tree.xpath(selector)
            if not results:
                return ""
            first = results[0]
            if attr == "text":
                return str(first).strip() if hasattr(first, "strip") else str(getattr(first, "text", "")).strip()
            getter = getattr(first, "get", None)
            return str(getter(attr) or "") if getter is not None else ""
        tree = HTMLParser(body)
        node = tree.css_first(selector)
        if node is None:
            return ""
        if attr == "text":
            return node.text(strip=True)
        return str(node.attributes.get(attr, "") or "")


__all__ = ["HtmlIngestSpider"]
