"""RSS/Atom 订阅源爬取 Spider.

从 RSS 2.0 / Atom 1.0 订阅源爬取条目，使用 feedparser 解析为统一的 dict。
对 feedparser 的复杂类型（content 列表、tags 列表、struct_time 日期）做归一化，
产出可直接写入目标表的简单字典。不翻页（订阅源为单次请求文档）。

parse_config 结构::

    {
        "include_feed_metadata": false  // 可选，是否合并 feed 级元数据（title/link/description 等）到每条 entry
    }

归一化规则：
- ``content``（list[dict]）: 合并所有 ``value`` 为单个字符串（换行分隔）
- ``tags``（list[dict]）: 提取 ``term`` 为 ``list[str]``
- ``*_parsed``（time.struct_time）: 转为 ISO 8601 字符串
- ``*_detail``/``authors``/``author_detail``/``links``/``guidislink``: 丢弃（重复或元信息字段）
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import feedparser  # type: ignore[import-not-found]
from scrapy.http import Request, Response  # type: ignore[import-not-found]

from apps.ingest.spiders.base import BaseIngestSpider

logger = logging.getLogger(__name__)

# 丢弃的 feedparser 元信息字段（重复值或内部标记）
_DROP_FIELDS = frozenset(
    {
        "title_detail",
        "summary_detail",
        "subtitle_detail",
        "author_detail",
        "authors",
        "links",
        "guidislink",
        "source",
        "enclosures",
        "href",
        "media_content",
        "media_thumbnail",
    }
)


class RssIngestSpider(BaseIngestSpider):
    """RSS/Atom 订阅源爬取 Spider.

    从 source_url 下载订阅源（RSS 2.0 或 Atom 1.0），用 feedparser 解析，
    将每个 entry 归一化为 dict 后 yield。支持可选合并 feed 级元数据。
    """

    name = "ingest_rss"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.source_url:
            self.start_urls = [self.source_url]

    def start_requests(self) -> Iterator[Request]:  # type: ignore[missing-override-decorator, override]
        """发起订阅源下载请求，附带已解密请求头."""
        for url in self.start_urls:
            yield Request(url, headers=self.headers or None, callback=self.parse, dont_filter=True)

    def parse(self, response: Response, **_kwargs: Any) -> Iterator[Any]:  # type: ignore[missing-override-decorator, override]
        """解析 RSS/Atom 响应，归一化条目并 yield.

        Args:
            response: Scrapy 下载器返回的响应对象（body 为订阅源字节）。
        """
        # feedparser.parse 接受 bytes，自动检测编码与格式（RSS/Atom）
        parsed = feedparser.parse(response.body)

        if parsed.bozo and not parsed.entries:
            # bozo 表示解析有异常；无条目时视为彻底失败，记录日志不抛异常
            bozo_exc = getattr(parsed, "bozo_exception", None)
            logger.error("RSS/Atom 解析失败: %s", bozo_exc)
            return

        feed_meta = self._extract_feed_metadata(parsed) if self.parse_config.get("include_feed_metadata") else {}

        for entry in parsed.entries:
            yield self._normalize_entry(entry, feed_meta)

    def _extract_feed_metadata(self, parsed: Any) -> dict[str, Any]:
        """提取 feed 级元数据并归一化（标题/链接/描述等）.

        提取的元数据会以 ``feed_`` 前缀合并到每条 entry，避免与 entry 字段冲突。
        """
        feed = getattr(parsed, "feed", None)
        if feed is None:
            return {}
        meta: dict[str, Any] = {}
        for key in ("title", "link", "subtitle", "description", "language", "updated", "published"):
            value = feed.get(key)
            if value:
                meta[f"feed_{key}"] = self._normalize_value(key, value)
        return meta

    def _normalize_entry(self, entry: Any, feed_meta: dict[str, Any]) -> dict[str, Any]:
        """将 feedparser entry 归一化为简单 dict.

        Args:
            entry: feedparser entry 对象（字典式访问）。
            feed_meta: feed 级元数据（已归一化），合并到结果中。

        Returns:
            dict[str, Any]: 归一化后的条目字典。
        """
        row: dict[str, Any] = {}
        for key in entry:
            if key in _DROP_FIELDS:
                continue
            row[key] = self._normalize_value(key, entry[key])
        # 合并 feed 级元数据（feed_ 前缀，不覆盖 entry 字段）
        for k, v in feed_meta.items():
            row.setdefault(k, v)
        return row

    def _normalize_value(self, key: str, value: Any) -> Any:
        """按字段类型归一化单个值.

        - ``content``（list[dict]）: 合并 value 为字符串
        - ``tags``（list[dict]）: 提取 term 为 list[str]
        - ``*_parsed``（struct_time）: 转 ISO 8601 字符串
        - 其他: 原样返回
        """
        if key == "content" and isinstance(value, list):
            return "\n".join(str(item.get("value", "")) for item in value if isinstance(item, dict))
        if key == "tags" and isinstance(value, list):
            return [str(item.get("term", "")) for item in value if isinstance(item, dict)]
        if key.endswith("_parsed") and isinstance(value, time.struct_time):
            return time.strftime("%Y-%m-%dT%H:%M:%S+0000", value)
        return value


__all__ = ["RssIngestSpider"]
