"""RssIngestSpider parse 逻辑测试.

用 mock Response 测试 RSS 2.0 / Atom 1.0 解析、字段归一化（content/tags/日期）、
feed 元数据合并、异常处理。不启动 Scrapy 引擎，直接调用 spider.parse 验证输出。
"""

from __future__ import annotations

from typing import Any

from apps.ingest.spiders.rss_spider import RssIngestSpider
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]


def _make_response(url: str, body: bytes) -> TextResponse:
    """构造 Scrapy TextResponse（body 为字节，feedparser 接受 bytes）."""
    request = Request(url=url)
    return TextResponse(url=url, body=body, request=request)


def _collect(generator: Any) -> list[dict[str, Any]]:
    """收集 generator 产出的 dict 条目."""
    return [item for item in generator if isinstance(item, dict)]


# RSS 2.0 订阅源样本
_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>测试订阅源</title>
    <link>http://example.com/</link>
    <description>测试用 RSS 源</description>
    <language>zh-cn</language>
    <item>
      <title>第一条</title>
      <link>http://example.com/1</link>
      <guid>tag:example.com,2024:1</guid>
      <description>第一条描述</description>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <author>alice</author>
      <category>news</category>
      <category>tech</category>
    </item>
    <item>
      <title>第二条</title>
      <link>http://example.com/2</link>
      <guid>tag:example.com,2024:2</guid>
      <description>第二条描述</description>
      <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
""".encode()

# Atom 1.0 订阅源样本（含 content 与 category）
_ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 测试源</title>
  <link href="http://example.com/"/>
  <subtitle>Atom 副标题</subtitle>
  <updated>2024-01-02T00:00:00Z</updated>
  <entry>
    <title>Atom 条目</title>
    <link href="http://example.com/atom/1"/>
    <id>tag:example.com,2024:atom1</id>
    <updated>2024-01-01T00:00:00Z</updated>
    <published>2024-01-01T00:00:00Z</published>
    <content type="html">&lt;p&gt;正文内容&lt;/p&gt;</content>
    <category term="news" label="News"/>
    <category term="tech"/>
    <author><name>bob</name></author>
  </entry>
</feed>
</xml>
""".encode()


class TestParseRss:
    """RSS 2.0 解析测试."""

    def test_extracts_all_entries(self) -> None:
        """应解析出所有 item 条目."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        assert len(items) == 2

    def test_entry_basic_fields(self) -> None:
        """应提取 title/link/id/published/author 等基本字段."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        first = items[0]
        assert first["title"] == "第一条"
        assert first["link"] == "http://example.com/1"
        assert first["id"] == "tag:example.com,2024:1"
        assert first["author"] == "alice"

    def test_published_parsed_normalized_to_iso(self) -> None:
        """published_parsed（struct_time）应归一化为 ISO 8601 字符串."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["published_parsed"].startswith("2024-01-01T00:00:00")

    def test_tags_normalized_to_list_of_terms(self) -> None:
        """tags（list[dict]）应归一化为 list[str]（term 列表）."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["tags"] == ["news", "tech"]
        # 第二条无 category，feedparser 不添加 tags 键
        assert "tags" not in items[1]

    def test_drop_fields_excluded(self) -> None:
        """重复/元信息字段（*_detail/links/guidislink 等）应被丢弃."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        for dropped in ("title_detail", "summary_detail", "links", "guidislink", "author_detail"):
            assert dropped not in items[0]


class TestParseAtom:
    """Atom 1.0 解析测试."""

    def test_extracts_entries(self) -> None:
        """应解析 Atom entry."""
        spider = RssIngestSpider(source_url="http://x/atom.xml", parse_config={})
        response = _make_response("http://x/atom.xml", _ATOM_FEED)
        items = _collect(spider.parse(response))
        assert len(items) == 1

    def test_content_normalized_to_string(self) -> None:
        """content（list[dict]）应归一化为合并 value 的字符串."""
        spider = RssIngestSpider(source_url="http://x/atom.xml", parse_config={})
        response = _make_response("http://x/atom.xml", _ATOM_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["content"] == "<p>正文内容</p>"

    def test_atom_tags_normalized(self) -> None:
        """Atom category 应归一化为 term 列表."""
        spider = RssIngestSpider(source_url="http://x/atom.xml", parse_config={})
        response = _make_response("http://x/atom.xml", _ATOM_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["tags"] == ["news", "tech"]

    def test_updated_parsed_normalized(self) -> None:
        """updated_parsed 应归一化为 ISO 字符串."""
        spider = RssIngestSpider(source_url="http://x/atom.xml", parse_config={})
        response = _make_response("http://x/atom.xml", _ATOM_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["updated_parsed"].startswith("2024-01-01T00:00:00")


class TestFeedMetadata:
    """feed 级元数据合并测试."""

    def test_no_merge_by_default(self) -> None:
        """默认不合并 feed 元数据，entry 不含 feed_ 前缀字段."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        assert "feed_title" not in items[0]
        assert "feed_link" not in items[0]

    def test_merge_when_enabled(self) -> None:
        """include_feed_metadata=true 时应合并 feed_ 前缀字段."""
        spider = RssIngestSpider(
            source_url="http://x/feed.xml",
            parse_config={"include_feed_metadata": True},
        )
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        assert items[0]["feed_title"] == "测试订阅源"
        assert items[0]["feed_link"] == "http://example.com/"
        assert items[0]["feed_description"] == "测试用 RSS 源"
        assert items[0]["feed_language"] == "zh-cn"

    def test_merge_does_not_override_entry_fields(self) -> None:
        """feed 元数据不应覆盖 entry 同名字段（setdefault 语义）."""
        spider = RssIngestSpider(
            source_url="http://x/feed.xml",
            parse_config={"include_feed_metadata": True},
        )
        response = _make_response("http://x/feed.xml", _RSS_FEED)
        items = _collect(spider.parse(response))
        # entry 自身 title 应保留，不被 feed_title 影响
        assert items[0]["title"] == "第一条"
        assert items[0]["feed_title"] == "测试订阅源"


class TestErrorHandling:
    """异常处理测试."""

    def test_invalid_xml_no_entries_yields_nothing(self) -> None:
        """非法 XML 且无条目时应产出空（记录日志不抛异常）."""
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", b"not xml at all <broken")
        items = _collect(spider.parse(response))
        assert items == []

    def test_empty_feed_yields_nothing(self) -> None:
        """空订阅源（无 item）应产出空."""
        empty_rss = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>空</title><link>http://x</link></channel></rss>""".encode()
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", empty_rss)
        items = _collect(spider.parse(response))
        assert items == []

    def test_bozo_with_entries_still_yields(self) -> None:
        """bozo 但有条目时仍应产出条目（宽容解析）."""
        # 非完美 XML 但 feedparser 能宽容解析出条目
        bozo_feed = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>bozo</title>
<item><title>有条目</title></item>
</channel></rss>""".encode()
        spider = RssIngestSpider(source_url="http://x/feed.xml", parse_config={})
        response = _make_response("http://x/feed.xml", bozo_feed)
        items = _collect(spider.parse(response))
        assert len(items) == 1
        assert items[0]["title"] == "有条目"


class TestStartRequests:
    """start_requests 请求构造测试."""

    def test_start_urls_set_from_source_url(self) -> None:
        """source_url 应设为 start_urls."""
        spider = RssIngestSpider(source_url="http://x/feed.xml")
        assert spider.start_urls == ["http://x/feed.xml"]

    def test_start_requests_attaches_headers(self) -> None:
        """start_requests 应附带请求头."""
        spider = RssIngestSpider(
            source_url="http://x/feed.xml",
            headers={"Authorization": "Bearer tok"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert requests[0].headers.get("Authorization") == b"Bearer tok"

    def test_start_requests_get_method(self) -> None:
        """RSS spider 始终用 GET 请求."""
        spider = RssIngestSpider(source_url="http://x/feed.xml")
        requests = list(spider.start_requests())
        assert requests[0].method == "GET"

    def test_empty_source_url_no_start_urls(self) -> None:
        """无 source_url 时 start_urls 为空."""
        spider = RssIngestSpider(source_url="")
        assert spider.start_urls == []
