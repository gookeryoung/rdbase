# iter-31 数据爬取骨架与 Scrapy/Django 集成

## 需求清单

- [x] 31 模块骨架与 Scrapy/Django 集成：ingest app 注册、4 个数据模型 + migration、Scrapy 子进程启动器、base spider、run_ingest 命令跑通

## 迭代目标

建立「外部数据摄取」能力骨架：新建 `apps/ingest` 模块，定义 4 个数据模型，实现 Scrapy 子进程隔离编排（spawn_ingest + execute_task），提供 base spider 占位与 run_ingest/run_scheduled_ingest 管理命令，配套 django-ninja API（CRUD/执行/日志/告警/统计），全程复用现有 DataSource、engine、scheduling、crypto 抽象。iter-31 不实现具体源类型抓取逻辑（留 iter-32+），仅验证 Scrapy 引擎可启停与日志/告警/调度闭环。

## 改动文件清单

- [backend/apps/ingest/models.py](file:///home/zhou/rdbase/backend/apps/ingest/models.py)：4 模型（IngestTask/IngestFieldMapping/IngestLog/IngestAlert）+ 6 枚举（SourceType/IngestStatus/IngestLogStatus/ConflictStrategy/AuthType/AlertLevel）+ IngestStats dataclass；IngestTask.set_headers/get_headers（Fernet 加解密请求头）、refresh_next_run（复用 sync.scheduling）；IngestLog.aggregate_stats、IngestAlert.raise_alert/acknowledge。
- [backend/apps/ingest/engine.py](file:///home/zhou/rdbase/backend/apps/ingest/engine.py)：spawn_ingest（subprocess 启动 run_ingest，禁 shell、list 形参）、execute_task（in-process 执行 + 日志/告警/重试）、_run_spider（CrawlerProcess 启动 Scrapy）、_resolve_spider（source_type 分派，iter-31 占位 BaseIngestSpider）、_build_scrapy_settings（robots/concurrent/timeout 等）。
- [backend/apps/ingest/spiders/base.py](file:///home/zhou/rdbase/backend/apps/ingest/spiders/base.py)：BaseIngestSpider 基类，接收 source_url/parse_config，iter-31 默认不发请求（验证引擎启停）。
- [backend/apps/ingest/api.py](file:///home/zhou/rdbase/backend/apps/ingest/api.py)：Router 挂 /api/v1/ingest，CRUD + run + logs + alerts + stats；管理员鉴权 require_admin；headers 明文不回显仅返回 has_headers。
- [backend/apps/ingest/schemas.py](file:///home/zhou/rdbase/backend/apps/ingest/schemas.py)：Pydantic Schemas。
- [backend/apps/ingest/admin.py](file:///home/zhou/rdbase/backend/apps/ingest/admin.py)、[apps.py](file:///home/zhou/rdbase/backend/apps/ingest/apps.py)、[__init__.py](file:///home/zhou/rdbase/backend/apps/ingest/__init__.py)：app 骨架。
- [backend/apps/ingest/management/commands/run_ingest.py](file:///home/zhou/rdbase/backend/apps/ingest/management/commands/run_ingest.py)：Scrapy 子进程入口，加载 task 调 execute_task。
- [backend/apps/ingest/management/commands/run_scheduled_ingest.py](file:///home/zhou/rdbase/backend/apps/ingest/management/commands/run_scheduled_ingest.py)：定时调度，扫描到期任务逐个 spawn_ingest 并 refresh_next_run。
- [backend/apps/ingest/migrations/0001_initial.py](file:///home/zhou/rdbase/backend/apps/ingest/migrations/0001_initial.py)：建表迁移。
- [backend/rdbase/settings/base.py](file:///home/zhou/rdbase/backend/rdbase/settings/base.py)：INSTALLED_APPS 加 apps.ingest。
- [backend/api/v1/__init__.py](file:///home/zhou/rdbase/backend/api/v1/__init__.py)：挂载 ingest_router 到 /ingest。
- [pyproject.toml](file:///home/zhou/rdbase/pyproject.toml)：新增 scrapy/selectolax/feedparser/jsonpath-ng 运行时依赖。
- [tests/test_ingest_models.py](file:///home/zhou/rdbase/tests/test_ingest_models.py)、[test_ingest_engine.py](file:///home/zhou/rdbase/tests/test_ingest_engine.py)、[test_ingest_api.py](file:///home/zhou/rdbase/tests/test_ingest_api.py)、[test_ingest_commands.py](file:///home/zhou/rdbase/tests/test_ingest_commands.py)：44 测试。
- [.trae/req/req-02-数据爬取.md](file:///home/zhou/rdbase/.trae/req/req-02-数据爬取.md)：需求记录。

## 关键决策与依据

1. **Scrapy 子进程隔离**：Twisted reactor 与 Django ASGI 不共存。spawn_ingest 用 subprocess（禁 shell、list 形参）启动 run_ingest 命令，CrawlerProcess 在子进程内运行，reactor 不污染 web 进程。每个爬取任务一个子进程，reactor 一次性启停。
2. **复用既有抽象**：调度复用 apps.sync.scheduling（compute_next_run/validate_cron/CronError）；凭证加密复用 apps.datasources.crypto（encrypt_password/decrypt_password）；目标连接复用 apps.datasources.engine（iter-32 写入目标表时用）；ConflictStrategy 枚举值与 sync 一致（自定义枚举保持模块自洽，待第三处出现再提取公共）。
3. **headers 加密**：敏感请求头（API Key/Cookie）整体 JSON 经 Fernet 加密存 headers_encrypted，API 仅回显 has_headers 标志，明文不外泄。
4. **iter-31 占位策略**：_resolve_spider 对所有 source_type 返回 BaseIngestSpider 并记录警告（专用 spider 在 iter-32+ 实现）；BaseIngestSpider 默认 start_urls=[]，验证 Scrapy 引擎可启动并优雅停止，无网络依赖。
5. **测试 mock 策略**：execute_task 测试 monkeypatch _run_spider（不真跑 Scrapy，因 reactor 不可在 pytest 同进程反复启停）；spawn_ingest 测试 mock subprocess.run；命令测试 mock execute_task/spawn_ingest。真实 Scrapy 端到端在 iter-32 用本地 server。
6. **审计暂依赖中间件**：ingest API 写操作由 AuditMiddleware 捕获为通用 WRITE；ingest 专用审计枚举（INGEST_CREATE/UPDATE/DELETE/RUN）与显式业务上下文留 iter-35 补全。

## 代码实现情况

- 模型层：6 枚举 + 4 模型 + IngestStats，完整类型注解与中文 docstring；get_headers 捕获 InvalidToken/JSONDecodeError 返回空字典，密文损坏不抛异常。
- 引擎层：execute_task 的 try/except 仅捕获 IngestError（_run_spider 内部把预期异常包装为 IngestError），finally 写日志并 _apply_task_status 更新任务重试计数与 last_sync_at；失败达 max_retries 调 IngestAlert.raise_alert。
- API 层：_validate_task_fields 校验 source_type/auth_type/conflict_strategy/datasource/cron；create 时 set_headers 加密、_sync_field_mappings 全量替换、可调度时 refresh_next_run；run 调 spawn_ingest 同步等待并返回最新 log。
- 命令层：run_ingest 加载 task 调 execute_task，按状态输出 SUCCESS/PARTIAL/FAILED；run_scheduled_ingest 遍历到期任务 spawn_ingest 并 refresh_next_run。

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed。
- `uv run ruff format --check backend tests`：151 files already formatted。
- `uv run pyrefly check`：0 errors（133 suppressed, 595 warnings）。
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：1024 passed, 5 deselected，覆盖率 97.30%（≥95%）。
- ingest 专项 44 测试覆盖模型/引擎/命令/API 全路径。

## 遗留事项

- 4 类源类型的专用 spider 与解析器未实现（iter-32 API/JSONPath + 字段映射 + 写入目标；iter-33 HTML/FILE；iter-34 RSS + 鉴权 + robots）。
- Item Pipeline（字段映射 + SQLAlchemy 写入目标表，复用 sync_service 方言 UPSERT）未实现（iter-32）。
- 真实 Scrapy 端到端集成测试（本地 HTTP server）未编写（iter-32）。
- ingest 专用审计枚举与显式 log_audit 未补（iter-35）。
- 前端管理界面未实现（iter-35）。

## 下一轮计划

iter-32：REST/JSON API 爬取器 + 字段映射 + 写入目标数据源。实现 ApiIngestSpider（start_urls=source_url，parse 用 JSONPath 解析 response.json）、FieldMappingPipeline（字段映射 + 复用 sync_service 方言化 UPSERT/SKIP 写入 target_datasource.target_table）、_resolve_spider 替换 API 映射、execute_task 接入 pipeline 统计（rows_written/rows_skipped）、本地 HTTP server 端到端集成测试。
