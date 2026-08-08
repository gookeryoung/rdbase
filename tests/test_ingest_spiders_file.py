"""FileIngestSpider parse 逻辑测试.

用 mock Response 测试 CSV/Excel/JSON 文件解析、编码/分隔符/工作表配置、
JSONPath 定位与异常处理。不启动 Scrapy 引擎，直接调用 spider.parse 验证输出。
"""

from __future__ import annotations

import json
from typing import Any

from apps.ingest.spiders.file_spider import FileIngestSpider
from scrapy.http import Request, TextResponse  # type: ignore[import-not-found]


def _make_text_response(url: str, body: str) -> TextResponse:
    """构造文本响应（用于 CSV/JSON）."""
    request = Request(url=url)
    return TextResponse(url=url, body=body.encode("utf-8"), encoding="utf-8", request=request)


def _make_bytes_response(url: str, body: bytes) -> TextResponse:
    """构造字节响应（用于 Excel 二进制）."""
    request = Request(url=url)
    return TextResponse(url=url, body=body, request=request)


def _collect(generator: Any) -> list[dict[str, Any]]:
    """收集 generator 产出的 dict 条目."""
    return [item for item in generator if isinstance(item, dict)]


class TestParseCsv:
    """CSV 文件解析测试."""

    def test_basic_csv(self) -> None:
        """标准 CSV 应按表头解析为行字典."""
        body = "id,name,score\n1,alice,95\n2,bob,87\n"
        spider = FileIngestSpider(source_url="http://x/data.csv", parse_config={"format": "csv"})
        response = _make_text_response("http://x/data.csv", body)
        items = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": "1", "name": "alice", "score": "95"}
        assert items[1] == {"id": "2", "name": "bob", "score": "87"}

    def test_custom_delimiter(self) -> None:
        """delimiter 配置应支持分号分隔."""
        body = "id;name\n1;alice\n2;bob\n"
        spider = FileIngestSpider(
            source_url="http://x/data.csv",
            parse_config={"format": "csv", "delimiter": ";"},
        )
        response = _make_text_response("http://x/data.csv", body)
        items = _collect(spider.parse(response))
        assert items[0] == {"id": "1", "name": "alice"}

    def test_custom_encoding(self) -> None:
        """encoding 配置应正确解码非 UTF-8 文本."""
        body = "name\n中文\n".encode("gbk")
        spider = FileIngestSpider(
            source_url="http://x/data.csv",
            parse_config={"format": "csv", "encoding": "gbk"},
        )
        # 直接用 bytes 构造响应（不经过 TextResponse 编码）
        request = Request(url="http://x/data.csv")
        response = TextResponse(url="http://x/data.csv", body=body, request=request)
        items = _collect(spider.parse(response))
        assert items[0]["name"] == "中文"

    def test_empty_csv_yields_nothing(self) -> None:
        """仅有表头的 CSV 应产出 0 条."""
        body = "id,name\n"
        spider = FileIngestSpider(source_url="http://x/data.csv", parse_config={"format": "csv"})
        response = _make_text_response("http://x/data.csv", body)
        items = _collect(spider.parse(response))
        assert items == []


class TestParseJson:
    """JSON 文件解析测试."""

    def test_basic_list_json(self) -> None:
        """JSON 数组应逐条 yield."""
        body = json.dumps([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
        spider = FileIngestSpider(source_url="http://x/data.json", parse_config={"format": "json"})
        response = _make_text_response("http://x/data.json", body)
        items = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": 1, "name": "a"}

    def test_single_object_json(self) -> None:
        """JSON 单对象应 yield 一条."""
        body = json.dumps({"id": 1, "name": "a"})
        spider = FileIngestSpider(source_url="http://x/data.json", parse_config={"format": "json"})
        response = _make_text_response("http://x/data.json", body)
        items = _collect(spider.parse(response))
        assert len(items) == 1
        assert items[0] == {"id": 1, "name": "a"}

    def test_jsonpath_items_path(self) -> None:
        """items_path 应从嵌套结构定位条目数组."""
        body = json.dumps({"data": {"records": [{"id": 1}, {"id": 2}]}})
        spider = FileIngestSpider(
            source_url="http://x/data.json",
            parse_config={"format": "json", "items_path": "$.data.records[*]"},
        )
        response = _make_text_response("http://x/data.json", body)
        items = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": 1}

    def test_invalid_json_yields_nothing(self) -> None:
        """非法 JSON 应返回空（记录日志不抛异常）."""
        spider = FileIngestSpider(source_url="http://x/data.json", parse_config={"format": "json"})
        response = _make_text_response("http://x/data.json", "not json")
        items = _collect(spider.parse(response))
        assert items == []

    def test_filters_non_dict_items(self) -> None:
        """列表中的非 dict 元素应被过滤."""
        body = json.dumps([{"id": 1}, "str", 42, {"id": 2}])
        spider = FileIngestSpider(source_url="http://x/data.json", parse_config={"format": "json"})
        response = _make_text_response("http://x/data.json", body)
        items = _collect(spider.parse(response))
        assert len(items) == 2


class TestParseExcel:
    """Excel 文件解析测试."""

    def _make_excel_bytes(self) -> bytes:
        """构造简单的 Excel 文件字节流."""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["id", "name", "score"])
        ws.append([1, "alice", 95])
        ws.append([2, "bob", 87])
        # 第二个工作表
        ws2 = wb.create_sheet("Sheet2")
        ws2.append(["code", "label"])
        ws2.append(["A", "Alpha"])
        import io

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_default_active_sheet(self) -> None:
        """未指定 sheet 时使用活动工作表."""
        spider = FileIngestSpider(source_url="http://x/data.xlsx", parse_config={"format": "excel"})
        response = _make_bytes_response("http://x/data.xlsx", self._make_excel_bytes())
        items = _collect(spider.parse(response))
        assert len(items) == 2
        assert items[0] == {"id": 1, "name": "alice", "score": 95}
        assert items[1] == {"id": 2, "name": "bob", "score": 87}

    def test_named_sheet(self) -> None:
        """指定 sheet 名时应读取对应工作表."""
        spider = FileIngestSpider(
            source_url="http://x/data.xlsx",
            parse_config={"format": "excel", "sheet": "Sheet2"},
        )
        response = _make_bytes_response("http://x/data.xlsx", self._make_excel_bytes())
        items = _collect(spider.parse(response))
        assert len(items) == 1
        assert items[0] == {"code": "A", "label": "Alpha"}


class TestUnsupportedFormat:
    """不支持的文件格式测试."""

    def test_unsupported_format_yields_nothing(self) -> None:
        """不支持的 format 应产出空（记录错误日志）."""
        spider = FileIngestSpider(
            source_url="http://x/data.xml",
            parse_config={"format": "xml"},
        )
        response = _make_text_response("http://x/data.xml", "<root/>")
        items = _collect(spider.parse(response))
        assert items == []

    def test_empty_format_yields_nothing(self) -> None:
        """未指定 format 应产出空."""
        spider = FileIngestSpider(source_url="http://x/data", parse_config={})
        response = _make_text_response("http://x/data", "anything")
        items = _collect(spider.parse(response))
        assert items == []


class TestStartRequests:
    """start_requests 请求构造测试."""

    def test_start_urls_set_from_source_url(self) -> None:
        """source_url 应设为 start_urls."""
        spider = FileIngestSpider(source_url="http://x/data.csv")
        assert spider.start_urls == ["http://x/data.csv"]

    def test_start_requests_attaches_headers(self) -> None:
        """start_requests 应附带请求头."""
        spider = FileIngestSpider(
            source_url="http://x/data.csv",
            headers={"Authorization": "Bearer tok"},
        )
        requests = list(spider.start_requests())
        assert len(requests) == 1
        assert requests[0].headers.get("Authorization") == b"Bearer tok"

    def test_start_requests_get_method(self) -> None:
        """FILE spider 始终用 GET 请求."""
        spider = FileIngestSpider(source_url="http://x/data.csv")
        requests = list(spider.start_requests())
        assert requests[0].method == "GET"

    def test_empty_source_url_no_start_urls(self) -> None:
        """无 source_url 时 start_urls 为空."""
        spider = FileIngestSpider(source_url="")
        assert spider.start_urls == []
