# iter-32 REST/JSON API 爬取器 + 字段映射 + 写入目标

## 需求清单

- [x] 32 REST/JSON API 爬取器 + 字段映射 + 写入目标：api_spider + JSONPath 解析、pipelines 字段映射与 SQLAlchemy 批量写入、手动执行 API、集成测试

## 迭代目标

实现 REST/JSON API 数据爬取的完整数据流：ApiIngestSpider 用 JSONPath 从 API 响应提取条目数组，FieldMappingPipeline 按字段映射（direct/constant）转换后批量写入目标 DataSource 表，支持 UPSERT/SKIP/ERROR 三种冲突策略。engine 层分派 API 源类型到 ApiIngestSpider，读取 pipeline 统计判定 SUCCESS/PARTIAL/FAILED。

## 改动文件清单

- [backend/apps/ingest/writer.py](file:///home/zhou/rdbase/backend/apps/ingest/writer.py)：方言化批量写入器，`write_rows` 纯函数实现 MySQL/PG/SQLite 的 UPSERT/SKIP/ERROR（复用 sync_service 方言模式，独立模块保持自洽）。
- [backend/apps/ingest/spiders/api_spider.py](file:///home/zhou/rdbase/backend/apps/ingest/spiders/api_spider.py)：ApiIngestSpider，JSONPath 提取条目数组、JSONPath 定位下一页 URL 翻页、start_requests 附带请求头与 POST body。
- [backend/apps/ingest/pipelines.py](file:///home/zhou/rdbase/backend/apps/ingest/pipelines.py)：FieldMappingPipeline，open_spider 初始化引擎与字段配置、process_item 应用映射并批量缓存、close_spider 刷新批次并写 crawler.stats。
- [backend/apps/ingest/spiders/base.py](file:///home/zhou/rdbase/backend/apps/ingest/spiders/base.py)：扩展接收 headers/request_config/mappings/target 配置，pipeline 通过 spider 属性读取写入配置避免直接依赖 ORM。
- [backend/apps/ingest/engine.py](file:///home/zhou/rdbase/backend/apps/ingest/engine.py)：`_resolve_spider` 分派 API 到 ApiIngestSpider；`_build_spider_kwargs` 从 task 提取完整配置注入 spider；`_build_scrapy_settings` 注册 FieldMappingPipeline；`_run_spider` 读取 pipeline 统计；`execute_task` 按统计判定 PARTIAL。
- [tests/test_ingest_writer.py](file:///home/zhou/rdbase/tests/test_ingest_writer.py)：26 测试，SQLite 真实库测试 UPSERT/SKIP/ERROR/无主键/空行，mock 连接验证 MySQL/PG SQL 生成。
- [tests/test_ingest_spiders_api.py](file:///home/zhou/rdbase/tests/test_ingest_spiders_api.py)：17 测试，JSONPath 提取/无 path/嵌套路径/非法 JSON/非法 JSONPath/非 dict 过滤/分页/max 限制/空 URL/请求头/POST 方法。
- [tests/test_ingest_pipelines.py](file:///home/zhou/rdbase/tests/test_ingest_pipelines.py)：10 测试，direct/constant 映射、批量刷新、缺失字段、统计收集、SKIP 冲突计数、无引擎零统计、from_crawler。
- [tests/test_ingest_e2e.py](file:///home/zhou/rdbase/tests/test_ingest_e2e.py)：3 slow 测试，本地 HTTP server 端到端：UPSERT 完整流程、重复执行更新、SKIP 冲突跳过。
- [tests/test_ingest_engine.py](file:///home/zhou/rdbase/tests/test_ingest_engine.py)：更新 _resolve_spider 测试（API→ApiIngestSpider）、新增 PARTIAL 判定测试、新增 _build_spider_kwargs 测试、ITEM_PIPELINES 验证。

## 关键决策与依据

1. **writer 独立模块而非复用 sync_service**：sync_service 的 UPSERT 逻辑耦合在实例方法中（依赖 self.config）。提取公共模块需重构 sync_service 并重跑其测试，超出 iter-32 范围。按 rule-01「三处相似才提取」，当前仅第二处（sync + ingest），在 ingest/writer.py 独立实现相同方言模式，待第三处出现再提取公共 writer。
2. **pipeline 通过 spider 属性传递配置**：不让 pipeline 直接查 Django ORM，而是由 _build_spider_kwargs 从 task 提取配置注入 spider，pipeline 在 open_spider 读 spider 属性。这样 pipeline 可单元测试（mock spider），不依赖 Django DB。
3. **e2e 测试不启动 Scrapy 引擎**：Twisted reactor 不可在 pytest 同进程反复启停。e2e 测试用 urllib 获取真实 HTTP 响应，构造 Scrapy TextResponse 喂给 spider.parse，再喂给 pipeline.process_item。验证完整数据流（HTTP→JSONPath→映射→方言写入→统计）而不依赖 reactor。标记 @pytest.mark.slow。
4. **PARTIAL 判定**：rows_skipped > 0 且 rows_written > 0 时为 PARTIAL（部分成功）。PARTIAL 也重置 retry_count（有数据写入视为可恢复）。
5. **SQLite :memory: 用 StaticPool**：writer 和 pipeline 测试用 `poolclass=StaticPool` 确保同一连接复用，表跨 `engine.begin()` 块可见。

## 代码实现情况

- writer.py：`write_rows` 接收 engine/rows/target_fields/pk_fields/conflict_strategy，按策略分派到 `_upsert_single_row`/`_skip_single_row`/`_insert_only`，各方言实现 `_upsert_mysql`/`_upsert_postgresql`/`_upsert_sqlite`。ERROR 策略不捕获行级异常，任一冲突回滚整批。
- api_spider.py：`parse` 解析 JSON → `_extract_items` 用 JSONPath 提取条目 → `_follow_next_page` 按 next_page_path 翻页。`start_requests` 附带 headers 与 POST body。非法 JSON/JSONPath 记录日志返回空，不抛异常。
- pipelines.py：`open_spider` 从 spider 读配置，查 DataSource 获取引擎；`process_item` 应用映射并缓存批次；`_flush` 调 `write_rows` 写入并累加统计；`close_spider` 刷剩余批次并写 crawler.stats。
- engine.py：`_build_spider_kwargs` 加载 field_mappings 转为 list[dict]；`_run_spider` 读 `item_scraped_count`/`ingest_rows_written`/`ingest_rows_skipped` 三个 stats。

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed。
- `uv run ruff format --check backend tests`：All files formatted。
- `uv run pyrefly check`：0 errors（146 suppressed, 613 warnings）。
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：1071 passed, 8 deselected，覆盖率 96.98%。
- `uv run pytest tests/test_ingest_*.py -v`（含 slow e2e）：94 passed。
- iter-32 新增 56 测试（writer 26 + spider 17 + pipeline 10 + e2e 3），更新 engine 测试 12 项。

## 遗留事项

- HTML/FILE/RSS 三类源类型的专用 spider 未实现（iter-33 HTML/FILE，iter-34 RSS）。
- ingest 专用审计枚举与显式 log_audit 未补（iter-35）。
- 前端管理界面未实现（iter-35）。
- writer 与 sync_service 的方言 UPSERT 逻辑相似，待第三处出现时提取公共模块。
- 真实 Scrapy 子进程端到端测试（spawn_ingest + 本地 server + 真实 Django DB）未编写，因 subprocess 需访问测试数据库有复杂度，当前 e2e 覆盖 spider+pipeline+writer 数据流。

## 下一轮计划

iter-33：网页 HTML + 文件下载爬取器。实现 HtmlIngestSpider（selectolax CSS/XPath 选择器解析）与 FileIngestSpider（CSV/Excel/JSON 流式下载解析），支持分页/翻页规则，扩展 _resolve_spider 分派，配套测试。
