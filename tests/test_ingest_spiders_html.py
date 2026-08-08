"""HtmlIngestSpider parse 逻辑测试.

用 mock Response 测试 CSS/XPath 行容器提取、字段文本/属性提取、分页。
不启动 Scrapy 引擎，直接调用 spider.parse 验证输出。
"""

from __future__ import annotations

from typing import Any

from apps.ingest.spiders.html_spider import HtmlIngestSpider
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]


def _make_response(url: str, body: str) -> TextResponse:
    """构造 Scrapy TextResponse（模拟下载器返回）."""
    request = Request(url=url)
    return TextResponse(url=url, body=body.encode("utf-8"), encoding="utf-8", request=request)


def _collect(generator: Any) -> tuple[list[dict[str, Any]], list[Request]]:
    """分离 generator 产出为 items 与 requests."""
    items: list[dict[str, Any]] = []
    requests: list[Request] = []
    for item in generator:
        if isinstance(item, Request):
            requests.append(item)
        elif isinstance(item, dict):
            items.append(item)
    return items, requests


_TABLE_HTML = """
<html><body>
<table>
  <thead><tr><th>id</th><th>name</th></tr></thead>
  <tbody>
    <tr><td>1</td><td>alice</td><td><a href="/u/1">view</a></td></tr>
    <tr><td>2</td><td>bob</td><td><a href="/u/2">view</a></td></tr>
  </tbody>
</table>
<a class="next-page" href="/page/2">下一页</a>
</body></html>
"""


class TestExtractRowsCss:
    """CSS 选择器行容器提取测试."""

    def test_extract_text_fields(self) -> None:
        """CSS 选择器应提取字段文本."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "selector_type": "css",
                "fields": {
                    "id": "td:nth-child(1)",
                    "name": "td:nth-child(2)",
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": "1", "name": "alice"}
        assert items[1] == {"id": "2", "name": "bob"}

    def test_extract_attribute_field(self) -> None:
        """dict 形式 field_spec 应提取属性值."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "selector_type": "css",
                "fields": {
                    "link": {"selector": "a", "attr": "href"},
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0] == {"link": "/u/1"}
        assert items[1] == {"link": "/u/2"}

    def test_extract_html_attr(self) -> None:
        """attr=html 应返回节点 HTML."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "selector_type": "css",
                "fields": {
                    "raw": {"selector": "a", "attr": "html"},
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0]["raw"] is not None
        assert "view" in items[0]["raw"]

    def test_missing_field_returns_empty(self) -> None:
        """选择器无匹配时字段为空字符串."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "selector_type": "css",
                "fields": {
                    "missing": "td.nonexistent",
                    "id": "td:nth-child(1)",
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0]["missing"] == ""
        assert items[0]["id"] == "1"

    def test_empty_selector_returns_empty(self) -> None:
        """field_spec 的 selector 为空时应返回空字符串."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "selector_type": "css",
                "fields": {
                    "empty": {"selector": "", "attr": "text"},
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0]["empty"] == ""


class TestExtractRowsXPath:
    """XPath 选择器行容器提取测试."""

    def test_extract_text_fields(self) -> None:
        """XPath 应提取字段文本."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "//table/tbody/tr",
                "selector_type": "xpath",
                "fields": {
                    "id": "td[1]/text()",
                    "name": "td[2]/text()",
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": "1", "name": "alice"}
        assert items[1] == {"id": "2", "name": "bob"}

    def test_extract_attribute_field(self) -> None:
        """XPath 应提取属性值."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "//table/tbody/tr",
                "selector_type": "xpath",
                "fields": {
                    "link": "td[3]/a/@href",
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0] == {"link": "/u/1"}
        assert items[1] == {"link": "/u/2"}

    def test_missing_field_returns_empty(self) -> None:
        """XPath 无匹配时字段为空字符串."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "//table/tbody/tr",
                "selector_type": "xpath",
                "fields": {
                    "missing": "td[99]/text()",
                    "id": "td[1]/text()",
                },
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items[0]["missing"] == ""
        assert items[0]["id"] == "1"


class TestMissingConfig:
    """缺少必要配置时的行为测试."""

    def test_no_container_selector_yields_nothing(self) -> None:
        """未配置 container_selector 时不产出条目."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={"fields": {"id": "td"}},
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items == []

    def test_no_fields_yields_nothing(self) -> None:
        """未配置 fields 时不产出条目."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={"container_selector": "table tbody tr"},
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert items == []

    def test_default_selector_type_is_css(self) -> None:
        """未指定 selector_type 时默认使用 CSS."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2


class TestPagination:
    """分页翻页测试."""

    def test_follows_next_page_css(self) -> None:
        """next_page_selector 应 yield 下一页 Request."""
        spider = HtmlIngestSpider(
            source_url="http://x/list",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
                "next_page_selector": "a.next-page",
                "next_page_attr": "href",
            },
        )
        response = _make_response("http://x/list", _TABLE_HTML)
        _, requests = _collect(spider.parse(response))
        assert len(requests) == 1
        assert requests[0].url == "http://x/page/2"

    def test_no_next_page_selector_no_request(self) -> None:
        """未配置 next_page_selector 时不翻页."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_max_limits_pagination(self) -> None:
        """next_page_max 应限制翻页次数."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
                "next_page_selector": "a.next-page",
                "next_page_max": 1,
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        # 默认 page=1, max=1, 1 >= 1 -> 不翻页
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_max_allows_within_limit(self) -> None:
        """当前页小于 max 时应翻页."""
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
                "next_page_selector": "a.next-page",
                "next_page_max": 3,
            },
        )
        response = _make_response("http://x", _TABLE_HTML)
        # 模拟第二页：cb_kwargs page=2, max=3, 2 < 3 -> 翻页
        _, requests = _collect(spider.parse(response, page=2))
        assert len(requests) == 1

    def test_next_page_missing_url_no_request(self) -> None:
        """未找到下一页链接时不翻页."""
        html_no_next = "<html><body><table><tbody><tr><td>1</td></tr></tbody></table></body></html>"
        spider = HtmlIngestSpider(
            source_url="http://x",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
                "next_page_selector": "a.next-page",
            },
        )
        response = _make_response("http://x", html_no_next)
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_default_attr_is_href(self) -> None:
        """未指定 next_page_attr 时默认取 href."""
        spider = HtmlIngestSpider(
            source_url="http://x/list",
            parse_config={
                "container_selector": "table tbody tr",
                "fields": {"id": "td:nth-child(1)"},
                "next_page_selector": "a.next-page",
            },
        )
        response = _make_response("http://x/list", _TABLE_HTML)
        _, requests = _collect(spider.parse(response))
        assert len(requests) == 1
        assert requests[0].url == "http://x/page/2"


class TestStartRequests:
    """start_requests 请求构造测试."""

    def test_start_urls_set_from_source_url(self) -> None:
        """source_url 应设为 start_urls."""
        spider = HtmlIngestSpider(source_url="http://x/page")
        assert spider.start_urls == ["http://x/page"]

    def test_start_requests_attaches_headers(self) -> None:
        """start_requests 应附带请求头."""
        spider = HtmlIngestSpider(
            source_url="http://x/page",
            headers={"Authorization": "Bearer tok"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert requests[0].headers.get("Authorization") == b"Bearer tok"

    def test_start_requests_get_method(self) -> None:
        """HTML spider 始终用 GET 请求."""
        spider = HtmlIngestSpider(source_url="http://x/page")
        requests = list(spider.start_requests())
        assert requests[0].method == "GET"

    def test_empty_source_url_no_start_urls(self) -> None:
        """无 source_url 时 start_urls 为空."""
        spider = HtmlIngestSpider(source_url="")
        assert spider.start_urls == []
