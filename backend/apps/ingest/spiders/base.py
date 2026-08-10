"""爬取 Spider 基类.

所有源类型的 Spider 继承 BaseIngestSpider，按源类型实现 parse 逻辑。
基类接收统一的配置注入（source_url/parse_config/headers/请求配置/写入配置），
子类按源类型设置 start_urls 并覆写 :meth:`parse`。

pipeline 通过 spider 属性读取写入配置（mappings/target_datasource_id 等），
避免 pipeline 直接依赖 Django ORM，便于单元测试。
"""

from __future__ import annotations

from typing import Any, cast

from scrapy import Spider  # type: ignore[import-not-found]
from scrapy.http import Response  # type: ignore[import-not-found]

from apps.ingest.models import IngestTask


class BaseIngestSpider(Spider):
    """爬取 Spider 基类.

    配置由 :func:`apps.ingest.engine._run_spider` 通过 spider kwargs 注入：
    - source_url: 起始 URL
    - parse_config: 解析配置（选择器/JSONPath/文件格式等）
    - headers: 请求头字典（已解密）
    - request_config: 请求配置（method/body/分页等）
    - mappings: 字段映射列表（list[dict]，含 source_field/target_field/mapping_type/fixed_value/is_pk）
    - target_datasource_id: 目标数据源 ID
    - target_table: 目标表名
    - conflict_strategy: 冲突策略
    - batch_size: 批量大小
    - clean_config: 清洗配置（CleaningPipeline 读取，空字典时透传不清洗）
    - task_id: 任务 ID（用于去重命名空间）

    基类默认不发请求（start_urls 为空），子类应覆写 :meth:`parse`。
    """

    name = "ingest_base"

    def __init__(  # noqa: PLR0913
        self,
        *,
        source_url: str = "",
        parse_config: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        request_config: dict[str, Any] | None = None,
        mappings: list[dict[str, Any]] | None = None,
        target_datasource_id: int | None = None,
        target_table: str = "",
        conflict_strategy: str = "upsert",
        batch_size: int = 500,
        clean_config: dict[str, Any] | None = None,
        task_id: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.source_url = source_url
        self.parse_config: dict[str, Any] = parse_config or {}
        self.headers: dict[str, str] = headers or {}
        self.request_config: dict[str, Any] = request_config or {}
        self.mappings: list[dict[str, Any]] = mappings or []
        self.target_datasource_id = target_datasource_id
        self.target_table = target_table
        self.conflict_strategy = conflict_strategy
        self.batch_size = batch_size
        self.clean_config: dict[str, Any] = clean_config or {}
        self.task_id = task_id
        # 基类不发请求；子类按源类型设置 start_urls
        self.start_urls: list[str] = []

    def parse(self, _response: Response, **_kwargs: Any) -> Any:  # type: ignore[missing-override-decorator, override]
        """基类占位解析：不产出 item，由子类按源类型覆写.

        Args:
            response: Scrapy 下载器返回的响应对象。
        """
        return None

    @classmethod
    def from_task(cls, task: IngestTask) -> BaseIngestSpider:
        """从 IngestTask 构造 Spider 实例（便捷工厂）."""
        mappings = [
            {
                "source_field": m.source_field,
                "target_field": m.target_field,
                "mapping_type": m.mapping_type,
                "fixed_value": m.fixed_value,
                "is_pk": m.is_pk,
            }
            for m in task.field_mappings.all()
        ]
        return cls(
            source_url=task.source_url,
            parse_config=cast(dict[str, Any], task.parse_config or {}),
            headers=task.get_headers(),
            request_config=cast(dict[str, Any], task.request_config or {}),
            mappings=mappings,
            target_datasource_id=task.target_datasource_id,
            target_table=task.target_table,
            conflict_strategy=task.conflict_strategy,
            batch_size=task.batch_size,
            clean_config=cast(dict[str, Any], task.clean_config or {}),
            task_id=task.pk,
        )


__all__ = ["BaseIngestSpider"]
