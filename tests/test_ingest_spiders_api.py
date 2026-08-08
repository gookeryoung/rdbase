"""ApiIngestSpider parse 逻辑测试.

用 mock Response 测试 JSONPath 提取、分页、异常处理。
不启动 Scrapy 引擎，直接调用 spider.parse 验证输出。
"""

from __future__ import annotations

import json
from typing import Any

from apps.ingest.spiders.api_spider import ApiIngestSpider
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


class TestExtractItems:
    """JSONPath 条目提取测试."""

    def test_extract_with_items_path(self) -> None:
        """items_path 应正确定位数组并逐条 yield."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={"items_path": "$.data.items[*]"})
        response = _make_response("http://x", json.dumps({"data": {"items": [{"id": 1}, {"id": 2}]}}))
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": 1}

    def test_extract_no_items_path_list_response(self) -> None:
        """无 items_path 且响应为列表时逐条 yield."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={})
        response = _make_response("http://x", json.dumps([{"id": 1}, {"id": 2}]))
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2

    def test_extract_no_items_path_single_object(self) -> None:
        """无 items_path 且响应为单对象时 yield 一条."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={})
        response = _make_response("http://x", json.dumps({"id": 1, "name": "a"}))
        items, _ = _collect(spider.parse(response))
        assert len(items) == 1
        assert items[0] == {"id": 1, "name": "a"}

    def test_extract_nested_path(self) -> None:
        """复杂 JSONPath 应正确定位."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={"items_path": "$.results[*].record"})
        body = json.dumps({"results": [{"record": {"id": 1}}, {"record": {"id": 2}}]})
        response = _make_response("http://x", body)
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2

    def test_invalid_json_returns_empty(self) -> None:
        """非法 JSON 应返回空（不抛异常）."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={})
        response = _make_response("http://x", "not json")
        items, requests = _collect(spider.parse(response))
        assert items == []
        assert requests == []

    def test_invalid_jsonpath_returns_empty(self) -> None:
        """非法 JSONPath 应返回空（记录日志不抛异常）."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={"items_path": "$.[invalid"})
        response = _make_response("http://x", json.dumps({"data": [1]}))
        items, _ = _collect(spider.parse(response))
        assert items == []

    def test_filters_non_dict_items(self) -> None:
        """列表中的非 dict 元素应被过滤."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={})
        response = _make_response("http://x", json.dumps([{"id": 1}, "str", 42, {"id": 2}]))
        items, _ = _collect(spider.parse(response))
        assert len(items) == 2


class TestPagination:
    """分页翻页测试."""

    def test_follows_next_page(self) -> None:
        """有 next_page_path 时应 yield 下一页 Request."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            parse_config={"items_path": "$.data[*]", "next_page_path": "$.next"},
        )
        body = json.dumps({"data": [{"id": 1}], "next": "http://x/api?page=2"})
        response = _make_response("http://x/api", body)
        items, requests = _collect(spider.parse(response))
        assert len(items) == 1
        assert len(requests) == 1
        assert "page=2" in requests[0].url

    def test_no_next_page_yields_no_request(self) -> None:
        """无 next_page_path 时不翻页."""
        spider = ApiIngestSpider(source_url="http://x", parse_config={"items_path": "$.data[*]"})
        body = json.dumps({"data": [{"id": 1}], "next": "http://x/api?page=2"})
        response = _make_response("http://x", body)
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_max_limits_pagination(self) -> None:
        """next_page_max 应限制翻页次数."""
        spider = ApiIngestSpider(
            source_url="http://x",
            parse_config={"items_path": "$.data[*]", "next_page_path": "$.next", "next_page_max": 1},
        )
        body = json.dumps({"data": [{"id": 1}], "next": "http://x?page=2"})
        response = _make_response("http://x", body)
        # page=1 (默认), max=1, 1 >= 1 -> 不翻页
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_max_allows_within_limit(self) -> None:
        """当前页小于 max 时应翻页."""
        spider = ApiIngestSpider(
            source_url="http://x",
            parse_config={"items_path": "$.data[*]", "next_page_path": "$.next", "next_page_max": 3},
        )
        body = json.dumps({"data": [{"id": 1}], "next": "http://x?page=2"})
        response = _make_response("http://x", body)
        # 模拟第二页：cb_kwargs page=2, max=3, 2 < 3 -> 翻页
        _, requests = _collect(spider.parse(response, page=2))
        assert len(requests) == 1

    def test_next_page_empty_url_no_request(self) -> None:
        """next_page 值为空时不翻页."""
        spider = ApiIngestSpider(
            source_url="http://x",
            parse_config={"items_path": "$.data[*]", "next_page_path": "$.next"},
        )
        body = json.dumps({"data": [{"id": 1}], "next": None})
        response = _make_response("http://x", body)
        _, requests = _collect(spider.parse(response))
        assert requests == []

    def test_next_page_not_found_no_request(self) -> None:
        """next_page_path 无匹配时不翻页."""
        spider = ApiIngestSpider(
            source_url="http://x",
            parse_config={"items_path": "$.data[*]", "next_page_path": "$.pagination.next"},
        )
        body = json.dumps({"data": [{"id": 1}]})
        response = _make_response("http://x", body)
        _, requests = _collect(spider.parse(response))
        assert requests == []


class TestStartRequests:
    """start_requests 请求构造测试."""

    def test_start_urls_set_from_source_url(self) -> None:
        """source_url 应设为 start_urls."""
        spider = ApiIngestSpider(source_url="http://x/api")
        assert spider.start_urls == ["http://x/api"]

    def test_start_requests_attaches_headers(self) -> None:
        """start_requests 应附带请求头."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            headers={"Authorization": "Bearer tok"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert requests[0].headers.get("Authorization") == b"Bearer tok"

    def test_start_requests_post_method(self) -> None:
        """request_config.method=POST 时应使用 POST."""
        spider = ApiIngestSpider(
            source_url="http://x/api",
            request_config={"method": "POST", "body": {"q": "test"}},
        )
        requests = list(spider.start_requests())
        assert requests[0].method == "POST"

    def test_empty_source_url_no_start_urls(self) -> None:
        """无 source_url 时 start_urls 为空（基类行为）."""
        spider = ApiIngestSpider(source_url="")
        assert spider.start_urls == []
