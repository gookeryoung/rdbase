"""爬取 Spider 基类.

所有源类型的 Spider 继承 BaseIngestSpider，按源类型实现 parse 逻辑。
iter-31 提供最小可运行骨架：从 task 配置读取 source_url 与 parse_config，
默认 parse 不产出 item（空跑验证 Scrapy 引擎可启停），由子类覆写。
"""

from __future__ import annotations

from typing import Any, cast

from scrapy import Spider  # type: ignore[import-not-found]
from scrapy.http import Response  # type: ignore[import-not-found]

from apps.ingest.models import IngestTask


class BaseIngestSpider(Spider):
    """爬取 Spider 基类.

    task 配置由 :func:`apps.ingest.engine.execute_task` 通过 spider kwargs 注入：
    - source_url: 起始 URL
    - parse_config: 解析配置（选择器/JSONPath/文件格式等）

    iter-31 默认不发请求（start_urls 为空），仅验证 Scrapy 引擎可启动并优雅停止。
    子类应在 ``__init__`` 中按源类型设置 start_urls 并覆写 :meth:`parse`。
    """

    name = "ingest_base"

    def __init__(
        self,
        *,
        source_url: str = "",
        parse_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.source_url = source_url
        self.parse_config: dict[str, Any] = parse_config or {}
        # iter-31 基类不发请求；子类按源类型设置 start_urls
        self.start_urls: list[str] = []

    def parse(self, _response: Response, **_kwargs: Any) -> Any:  # type: ignore[missing-override-decorator, override]
        """iter-31 占位解析：不产出 item，由子类按源类型覆写.

        Args:
            response: Scrapy 下载器返回的响应对象。
        """
        # 基类不产出 item；子类应覆写此方法实现具体解析逻辑。
        return None

    @classmethod
    def from_task(cls, task: IngestTask) -> BaseIngestSpider:
        """从 IngestTask 构造 Spider 实例（便捷工厂）."""
        return cls(source_url=task.source_url, parse_config=cast(dict[str, Any], task.parse_config or {}))


__all__ = ["BaseIngestSpider"]
