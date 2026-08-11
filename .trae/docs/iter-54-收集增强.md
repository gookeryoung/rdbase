# iter-54：P8-Q4 收集增强

## 需求清单

- [x] 47 DATABASE 直连源：新增 `DatabaseIngestSpider`，执行 SQL 查询逐行 yield。
- [x] 48 Webhook 被动接收：`POST /ingest/webhook/{token}` 公开端点，token 自身鉴权。
- [x] 49 增量策略扩展：API_UPDATED_AT / HTML_FINGERPRINT / DB_TIMESTAMP 三种策略。
- [x] 50 IngestTask 新增 `incremental_config` JSONField 与 `webhook_token` CharField。
- [x] 51 新增 `IncrementalStrategy` 枚举与 `AuditAction.WEBHOOK_RECEIVE` 审计动作。
- [x] 52 Webhook 限流：令牌桶按 `webhook:{token}` 维度，容量 20 / 速率 2.0/s。
- [x] 53 Webhook 幂等：`Idempotency-Key` 命中回放结果，subject=`webhook:{token}`。

## 迭代目标

在 P8-Q3 质量监控告警基础上扩展数据收集能力，覆盖三种新增场景：

1. **DATABASE 直连源**：新增 `DatabaseIngestSpider`，通过 SQLAlchemy 连接目标数据库
   执行 SQL 查询，逐行 yield 为 dict；支持 `:last_sync_at` 占位符的增量查询。
2. **Webhook 被动接收**：新增 `POST /ingest/webhook/{token}` 公开端点（无 JWT 鉴权，
   token 自身即鉴权），外部应用主动推送数据，经完整 pipeline 链同步处理。
3. **增量策略扩展**：
   - `API_UPDATED_AT`：API 源按 `updated_since` 查询参数传递上次同步时间。
   - `HTML_FINGERPRINT`：HTML 源按页面内容 SHA-256 指纹跳过未变页面。
   - `DB_TIMESTAMP`：DB 源在 SQL WHERE 子句中按 `:last_sync_at` 占位符过滤。

## 改动文件清单

### 修改

- `backend/apps/audit/models.py`：`AuditAction` 新增 `WEBHOOK_RECEIVE = "webhook.receive"`。
- `backend/apps/ingest/models.py`：
  - `SourceType` 新增 `DATABASE` / `WEBHOOK` 两个枚举值
  - 新增 `IncrementalStrategy` 枚举（NONE / API_UPDATED_AT / HTML_FINGERPRINT / DB_TIMESTAMP）
  - `IngestTask` 新增 `incremental_config` JSONField 与 `webhook_token` CharField（unique）
  - `IngestTask.save` 自动维护 webhook_token（WEBHOOK 源类型自动生成，非 WEBHOOK 清空）
  - 新增 `generate_webhook_token` 静态方法（`secrets.token_urlsafe(32)` 约 43 字符）
  - 新增 `incremental_strategy` property（从 incremental_config 提取策略枚举值）
- `backend/apps/ingest/engine.py`：
  - 新增 `_SPIDER_MAP` 字典映射源类型到 Spider 类（消除 `_resolve_spider` 多分支 if）
  - `_resolve_spider` 改用字典查表，WEBHOOK 源类型返回基类占位并记日志
  - `_run_spider` 末尾新增 HTML_FINGERPRINT 指纹持久化（写回 task.incremental_config）
  - 新增 `_save_html_fingerprint` 辅助函数（重新读取 task 避免并发覆盖）
  - `_build_spider_kwargs` 注入 `incremental_config` 与 `__last_sync_at__`（透传增量参数）
- `backend/apps/ingest/spiders/base.py`：`BaseIngestSpider.__init__` 新增 `incremental_config` 参数。
- `backend/apps/ingest/spiders/api_spider.py`：
  - `start_requests` 按 `API_UPDATED_AT` 策略注入查询参数
  - 新增 `_inject_updated_param` 方法（`urlparse` + `urlencode` 正确拼接查询参数）
  - 新增 `_format_last_sync` 静态方法（支持 `iso` 与 strftime 模式）
- `backend/apps/ingest/spiders/html_spider.py`：
  - `parse` 首页（page=1）按 `HTML_FINGERPRINT` 策略计算 SHA-256 指纹
  - 指纹一致时跳过本次爬取（不产出 item，不翻页）
  - 指纹不一致时正常爬取，新指纹经 `crawler.stats` 回传给 engine 持久化
- `backend/apps/ingest/schemas.py`：
  - `IngestTaskCreateIn` / `IngestTaskUpdateIn` 新增 `incremental_config` 字段
  - `IngestTaskOut` 新增 `incremental_config` 与 `webhook_token` 字段
  - 新增 `WebhookReceiveOut` schema（task_id / log_id / rows_*  / quality_score）
- `backend/apps/ingest/api.py`：
  - 新增 `POST /ingest/webhook/{token}` 公开端点（`auth=None`）
  - `_get_webhook_task_or_404`：按 token 查找 WEBHOOK 类型任务，未命中或类型不符返回 404
  - `_parse_webhook_payload`：解析 payload（支持 list 或单个 dict，非 dict/空返回 400）
  - `receive_webhook`：幂等检查 → 令牌桶限流 → 同步执行 pipeline → 写审计
  - `_validate_task_fields` 新增 `incremental_config` 策略合法性校验
  - `_task_to_out` 输出 `incremental_config` 与 `webhook_token`
- `backend/rdbase/settings/base.py`：
  - 新增 `RATE_LIMIT_WEBHOOK_CAPACITY = 20`（突发上限）
  - 新增 `RATE_LIMIT_WEBHOOK_REFILL_RATE = 2.0`（每秒补充令牌数）
- `frontend/src/types/index.ts`：`IngestTask` 接口新增 `incremental_config` / `webhook_token` 字段。
- `frontend/src/pages/Ingest.tsx`：任务表单加"增量策略"与"Webhook Token"展示。
- `tests/test_audit_models.py`：`test_audit_action_choices_count` 断言更新为 33。
- `tests/test_ingest_models.py`：`test_source_type_choices` 新增 `database` / `webhook`。

### 新增

- `backend/apps/ingest/migrations/0005_ingest_task_webhook_and_incremental.py`：
  IngestTask 新增 `incremental_config` 与 `webhook_token` 字段 migration。
- `backend/apps/ingest/spiders/database_spider.py`（204 行）：`DatabaseIngestSpider`
  实现，通过 SQLAlchemy 连接目标数据库执行 SQL 查询，逐行 yield 为 dict。
- `backend/apps/ingest/webhook.py`（169 行）：`run_webhook_pipelines` 同步执行器，
  驱动 CleaningPipeline → ValidationPipeline → FieldMappingPipeline 完整链路。
- `tests/test_ingest_database_spider.py`（303 行，23 用例）：DatabaseIngestSpider 测试。
- `tests/test_ingest_webhook.py`（585 行，26 用例）：Webhook 端点 + pipeline 测试。
- `tests/test_ingest_incremental.py`（358 行，20 用例）：增量策略测试。
- `.trae/docs/iter-54-收集增强.md`：本迭代记录。

### 删除

- `.trae/docs/iter-49-同步监控与告警验收.md`（迭代文件数达 6，按规则清理最旧 iter-49，
  保留最新 5 条）。

## 关键决策与依据

1. **Webhook token 自身即鉴权**：公开端点 `auth=None`，不依赖 JWT/API Token。
   token 由 `secrets.token_urlsafe(32)` 生成（约 43 字符，密码学安全），
   仅 WEBHOOK 源类型任务在 `save` 时自动生成一次，避免覆盖已有 token。
2. **Webhook pipeline 同步执行**：不经 Scrapy CrawlerProcess，直接实例化三个
   pipeline 并调用 `open_spider` / `process_item` / `close_spider`。用
   `_SimpleStats`（dict 内存存储）替代 `crawler.stats`，用 `_build_spider_proxy`
   （SimpleNamespace）提供 pipeline 所需的 spider 属性。
3. **DB_TIMESTAMP 占位符注入**：SQL 中含 `:last_sync_at` 占位符时，将
   `request_config.__last_sync_at__` 作为绑定参数注入；首次执行（last_sync_at
   为空）注入 `1970-01-01T00:00:00` 全量拉取，避免 SQLAlchemy 报"参数未绑定"。
4. **HTML_FINGERPRINT 仅检查首页**：翻页请求不检查指纹（避免多页场景下后续页
   变化被忽略）。指纹经 `crawler.stats` 回传给 engine，engine 重新读取 task
   后写回 `incremental_config._last_fingerprint`（避免覆盖并发修改）。
5. **API_UPDATED_AT 参数拼接**：用 `urlparse` + `urlencode` 正确拼接查询参数，
   避免手动拼接导致的转义问题；支持 `iso`（默认）与 strftime 模式。
6. **`_resolve_spider` 字典化**：消除多分支 if/elif，用 `_SPIDER_MAP` 字典查表；
   WEBHOOK 源类型返回基类占位并记日志（不应经 Scrapy 触发）。
7. **Webhook 限流维度**：按 `webhook:{token}` 维度限流（非 IP 维度），因为
   token 已是任务级凭证；容量 20 / 速率 2.0/s（数据推送场景默认高于触发端点）。
8. **Webhook 幂等 subject**：`webhook:{token}`（无认证主体），与 sync:trigger
   的 `trigger:{token.prefix}` 维度并列。
9. **PARTIAL 状态判定**：`rows_skipped > 0`（清洗丢弃或写入冲突）时为 PARTIAL，
   否则为 SUCCESS；与 Scrapy engine 的 `_determine_status` 逻辑一致。

## 代码实现情况

- `database_spider.py`：
  - `_parse_datasource_id`：支持 `datasource://{id}` 与 `datasource:///{id}` 两种格式
  - `_build_sql`：从 `parse_config.sql` 读取（必填），缺失时报错
  - `_inject_incremental_param`：DB_TIMESTAMP 策略时注入 `:last_sync_at` 绑定参数
  - `start`：同步执行（非 Scrapy 异步），用 `engine.connect()` + `conn.execute(text(sql), params)`
- `webhook.py`：
  - `_SimpleStats`：提供 `get_value` / `set_value` / `inc_value` 接口子集
  - `_build_spider_proxy`：用 SimpleNamespace 构造 pipeline 所需的 spider 属性
  - `run_webhook_pipelines`：创建 IngestLog → 实例化三 pipeline → 逐条 process_item
    （DropItem 捕获计入 rows_skipped）→ close_spider 刷新批次 → 按统计判定状态
- `api.py` `receive_webhook`：
  - token 鉴权 → payload 解析 → 幂等检查 → 令牌桶限流 → 同步执行 pipeline →
    写审计 → 存幂等结果返回

## 整合优化情况

- `_resolve_spider` 字典化消除 PLR0911（太多 return 语句），同时提升可读性。
- `receive_webhook` 加 `# noqa: PLR0912`（太多分支）——流程固定，拆分反而降低可读性。
- `webhook.py` 用 `cast(dict[str, Any], task.clean_config or {})` 解决 pyrefly
  对 `dict(Any | None)` 的类型推断错误。
- `run_webhook_pipelines` 真实 pipeline 测试（5 用例）覆盖 SUCCESS/PARTIAL/空
  items/无映射/last_sync_at 更新，确保 webhook.py 覆盖率从 33% 提升到 95%+。
- 测试 `_clear_engine_cache` fixture（autouse）确保每个测试前后清空 SQLAlchemy
  引擎缓存，避免内存 SQLite 跨测试共享。

## 测试验证结果

### 单元测试

```
tests/test_ingest_database_spider.py    23 passed
tests/test_ingest_webhook.py            26 passed
tests/test_ingest_incremental.py        20 passed
```

覆盖：
- DatabaseIngestSpider：SQL 执行 / 参数绑定 / 增量策略 / 错误处理 / URL 解析
- Webhook 端点：token 鉴权 / payload 解析 / pipeline 执行 / 幂等回放 / 限流 / 审计
- Webhook pipeline 真实执行：写表 / PARTIAL 判定 / 空 items / 无映射 / last_sync_at
- 增量策略：API_UPDATED_AT 参数注入 / HTML_FINGERPRINT 指纹比对 / DB_TIMESTAMP 占位符

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 已格式化
uv run pyrefly check                          # 0 errors
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1933 passed, 15 deselected, 54 warnings in 86.49s
  TOTAL 9228 stmts, 339 miss, 1862 branch, 132 brpart, 95.43%
```

覆盖率 95.43%（≥95% 门禁），较 iter-53 的 95.59% 略降 0.16%（新增 69 用例，
webhook.py / database_spider.py / 增量策略分支较多）。测试数 1933（较 1879
新增 54）。

### 前端

```
cd frontend && bun run typecheck   # tsc --noEmit 通过
```

## 遗留事项

- P8-Q5 文档与测试：覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例。
- Webhook 端点可考虑加 IP 白名单（当前仅 token 鉴权 + 限流）。
- DatabaseIngestSpider 可考虑加查询超时（当前依赖 SQLAlchemy 默认超时）。

## 下一轮计划

- iter-55：P8-Q5 文档与测试 — 覆盖率回归 + 用户手册同步 + 端到端用例。
