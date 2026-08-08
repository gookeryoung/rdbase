# iter-33 HTML 与 FILE 爬取器

## 需求清单

- [x] 实现 HtmlIngestSpider（selectolax CSS / lxml XPath 解析 + 分页）
- [x] 实现 FileIngestSpider（CSV / Excel / JSON 文件下载解析）
- [x] engine._resolve_spider 分派 HTML / FILE 源类型
- [x] HtmlIngestSpider 单元测试
- [x] FileIngestSpider 单元测试
- [x] 全套门禁验证通过

## 迭代目标

完成数据爬取模块的 HTML 与 FILE 两类源类型 spider 实现，使 ingest 模块支持从网页表格与文件下载（CSV/Excel/JSON）爬取数据。配合 iter-32 已完成的 API spider 与字段映射写入，至此 API/HTML/FILE 三类源类型完整可用，仅 RSS 留待后续迭代。

## 改动文件清单

新增：
- `backend/apps/ingest/spiders/html_spider.py`：HtmlIngestSpider，selectolax CSS 与 lxml XPath 双模式行容器解析，支持字段文本/属性/html 提取，CSS 选择器下一页翻页（含 next_page_max 限制）。
- `backend/apps/ingest/spiders/file_spider.py`：FileIngestSpider，按 format 分派 CSV（csv.DictReader，支持 encoding/delimiter）/Excel（openpyxl，支持 sheet 选择）/JSON（支持 items_path JSONPath）三种解析。
- `tests/test_ingest_spiders_html.py`：21 项 HtmlIngestSpider 测试，覆盖 CSS/XPath 文本与属性提取、缺失字段、缺配置、分页（含 max 限制与默认 attr）、start_requests。
- `tests/test_ingest_spiders_file.py`：17 项 FileIngestSpider 测试，覆盖 CSV（含分隔符/编码/空文件）、Excel（活动表/命名表）、JSON（数组/单对象/JSONPath/非法 JSON/非 dict 过滤）、不支持格式、start_requests。

修改：
- `backend/apps/ingest/engine.py`：`_resolve_spider` 增加 HTML→HtmlIngestSpider、FILE→FileIngestSpider 分派；新增对应 import。RSS 仍回退 BaseIngestSpider 占位并记录警告。
- `tests/test_ingest_engine.py`：`TestResolveSpider` 拆分为 api/html/file/rss 四个专用测试，RSS 单独验证 BaseIngestSpider 回退与警告。

## 关键决策与依据

1. **HTML 双选择器模式**：CSS 用 selectolax（轻量快速），XPath 用 lxml（功能完整）。parse_config.selector_type 区分（默认 css）。理由：selectolax 不支持 XPath，lxml 不如 selectolax 快；两者互补覆盖主流场景。
2. **字段规格 dict/字符串双形态**：fields 配置中，简单场景直接写字符串选择器（默认取 text），复杂场景写 `{"selector": ..., "attr": "text|html|<attr_name>"}`。降低配置门槛同时保留灵活性。
3. **下一页仅支持 CSS 选择器**：XPath 翻页场景罕见且实现复杂，本轮仅支持 CSS 选择器定位下一页链接（next_page_selector + next_page_attr，默认 href）。相对 URL 用 urljoin 转绝对。
4. **FileIngestSpider 不翻页**：文件下载为单次请求，无分页概念。
5. **Excel 用 openpyxl read_only 模式**：流式读取降低大文件内存占用；data_only=True 取公式计算结果值。
6. **JSON 文件支持 JSONPath**：复用 api_spider 同款 jsonpath_ng.ext，items_path 配置语义一致。
7. **engine._resolve_spider 分派顺序**：API/HTML/FILE 专用 spider 优先匹配，剩余合法源类型（当前仅 RSS）回退 BaseIngestSpider 占位，非法源类型抛 IngestError。

## 代码实现情况

### HtmlIngestSpider
- `parse`：取 response.text，按 selector_type 调 `_extract_rows`，再 `_follow_next_page`。
- `_extract_rows_css` / `_extract_rows_xpath`：按 container_selector 定位行容器，逐行用 `_extract_field_*` 提取字段。
- `_extract_field_css` / `_extract_field_xpath`：静态方法，支持 dict/字符串双形态 field_spec，attr 为 text/html/属性名。
- `_follow_next_page`：按 next_page_selector 提取下一页 URL，urljoin 转绝对，cb_kwargs 传递 page 递增，受 next_page_max 限制。
- `_extract_next_url`：从 HTML 提取下一页 URL，CSS 与 XPath 双模式。

### FileIngestSpider
- `parse`：按 format 分派 `_parse_csv` / `_parse_excel` / `_parse_json`，不支持的格式记录 error 不抛异常。
- `_parse_csv`：按 encoding 解码，delimiter 分隔，csv.DictReader 逐行 yield dict。
- `_parse_excel`：openpyxl load_workbook（read_only + data_only），按 sheet 名或活动表读取，首行为表头，逐行构造 dict。
- `_parse_json`：按 encoding 解码，json.loads；无 items_path 时按响应是列表/单对象处理；有 items_path 时用 JSONPath 定位，过滤非 dict 元素。

### engine._resolve_spider 分派更新
```python
if source_type == SourceType.API:
    return ApiIngestSpider
if source_type == SourceType.HTML:
    return HtmlIngestSpider
if source_type == SourceType.FILE:
    return FileIngestSpider
if source_type in dict(SourceType.choices):  # RSS 等
    logger.warning("源类型 %s 的专用 spider 尚未实现，使用 BaseIngestSpider 占位", source_type)
    return BaseIngestSpider
raise IngestError(f"不支持的源类型: {source_type!r}")
```

## 整合优化情况

- HtmlIngestSpider 与 ApiIngestSpider 共享 BaseIngestSpider 的配置注入机制（source_url/parse_config/headers/mappings/写入配置），无需重复实现。
- FileIngestSpider 的 JSON 解析复用 jsonpath_ng.ext，与 ApiIngestSpider 的 items_path 语义一致，降低用户认知负担。
- engine._resolve_spider 保持单一分派入口，新增源类型只需在此函数增加分支。

## 测试验证结果

- ruff check：通过
- ruff format --check：通过（23 文件已格式化）
- pyrefly check：0 errors（41 suppressed，130 warnings not shown）
- pytest（非 slow）：1111 passed，8 deselected
- 覆盖率：ingest 模块 93%（html_spider 84%、file_spider 91%、engine 82%），全项目 97%（高于 95% 门禁）
  - engine.py 142-160（_run_spider 实际启动 Scrapy）未覆盖：需启动 Twisted reactor，与 pytest 不兼容，已在 iter-31 决策中说明。
  - html_spider.py 200 行（XPath attr getter None 分支）：边界场景，实际 lxml element 必有 get 方法。

## 遗留事项

- RSS/Atom 源类型 spider 未实现（BaseIngestSpider 占位），留待后续迭代引入 feedparser。
- HtmlIngestSpider 的 XPath 模式不支持 html 属性提取（仅 text 与属性名），因 lxml element 无 html 属性；CSS 模式支持 html。
- FileIngestSpider 未支持流式大文件处理（Excel read_only 已部分缓解），超大文件可能内存压力大。

## 下一轮计划

iter-34 候选方向（按优先级）：
1. RSS/Atom spider 实现（feedparser，复用 BaseIngestSpider 框架）。
2. ingest 模块 API 端到端测试（HTML/FILE 源类型的完整 API 流程）。
3. 前端爬取任务管理界面（任务列表/创建/执行/日志查看）。
