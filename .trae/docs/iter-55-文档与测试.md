# iter-55：P8-Q5 文档与测试

## 需求清单

- [x] 49 覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例（req-03 P8-Q5 item 49，
  P8 闭环最后一项）

## 迭代目标

完成 P8 数据清洗与质量提升的最后一块拼图：

1. **外部应用接入指南同步**：在 `docs/external-api-guide.rst` 新增「Webhook 被动接收」
   章节，文档化 `POST /api/v1/ingest/webhook/{token}` 端点的用法、payload 格式、
   幂等、限流、审计与错误码。
2. **更新日志同步**：在 `docs/changelog.rst` 追加 v0.1.0 P8-Q1~Q4 已交付条目，
   覆盖清洗 Pipeline / 质量校验 / 质量监控告警 / 收集增强四阶段交付物。
3. **端到端测试补全**：扩展 `tests/test_ingest_e2e.py`，新增 6 个 `@pytest.mark.slow`
   用例覆盖 DATABASE 源 / Webhook 接收 / DB_TIMESTAMP 增量策略三类端到端场景。
4. **覆盖率回归**：`make check` 全套门禁通过，覆盖率不低于 iter-54 基线 95.43%。

## 改动文件清单

### 修改

- `docs/external-api-guide.rst`：
  - 概述段补充「Webhook 被动接收（外推入 rdbase）」描述。
  - 端点表新增 `POST /api/v1/ingest/webhook/{token}` 行。
  - 新增「Webhook 被动接收」章节（约 95 行）：端点说明、请求/响应示例、payload 格式、
    处理流程 6 步、错误码、约束 4 项。
  - 错误码汇总表扩展：404/400/409/500 行补充 webhook 相关场景。
  - 最佳实践新增第 9 条：Webhook 接收 token 与 API Token 的区分。
- `docs/changelog.rst`：v0.1.0 由「项目初始化」单项扩展为 P1~P8 完整交付清单，
  其中 P8 按 Q1~Q5 五个子项列出（清洗 Pipeline / 质量校验 / 质量监控告警 /
  收集增强 / 文档与测试）。
- `tests/test_ingest_e2e.py`：
  - 模块 docstring 扩展说明 P8-Q5 三类 e2e 测试。
  - 新增导入：`AuditAction` / `AuditLog` / `dispose_all` / `get_engine` / `DataSource`
    / `EngineType` / `CleaningPipeline` / `ValidationPipeline` / `DatabaseIngestSpider`
    / `IngestFieldMapping` / `IngestLog` / `IngestLogStatus` / `IngestTask` / `SourceType`
    / `Client`。
  - 新增 `_FullStats` 类：完整 stats 收集器（get_value/set_value/inc_value），
    供三个 pipeline 共用。
  - 新增 `_clear_engine_cache_e2e` fixture：每测试前后 `dispose_all()` 清空引擎缓存。
  - 新增 6 个辅助函数：`_make_datasource` / `_init_source_users` /
    `_init_source_users_with_ts` / `_init_target_out` / `_add_mappings` /
    `_run_pipelines`。
  - 新增 `TestDatabaseIngestE2E`（2 用例）：DB spider full flow + 清洗丢弃。
  - 新增 `TestWebhookReceiveE2E`（2 用例）：HTTP POST full flow + dict payload。
  - 新增 `TestIncrementalE2E`（2 用例）：DB_TIMESTAMP 增量 + 首次全量。
- `.trae/req/req-03-数据清洗与质量提升.md`：item 47/48/49 由 `[ ]` 改为 `[x]`。

### 新增

- `.trae/docs/iter-55-文档与测试.md`：本迭代记录。

### 删除

- `.trae/docs/iter-50-P6测试与文档.md`（迭代文件数达 6，按规则清理最旧 iter-50，
  保留最新 5 条）。

## 关键决策与依据

1. **扩展 test_ingest_e2e.py 而非新建文件**：项目历史将 ingest 端到端测试集中在
   `tests/test_ingest_e2e.py`（已有 3 个 API spider slow 用例），扩展保持单一入口
   便于检索与维护。
2. **e2e 测试用真实 DataSource + Django DB**：与 `test_ingest_webhook.py` /
   `test_ingest_database_spider.py` 风格一致，通过 `@pytest.mark.django_db` +
   `dispose_all()` fixture 确保测试隔离。不复用 `test_ingest_e2e.py` 顶部基于
   monkeypatch 的 `_patch_pipeline_deps` 模式（该模式为 API spider 设计，不依赖
   Django ORM；DB/Webhook/增量场景需要真实 DataSource 与 IngestTask）。
3. **`_FullStats` 而非复用 `_StatsCollector`**：顶部 `_StatsCollector` 仅有
   `set_value`，但 CleaningPipeline / ValidationPipeline 还调用 `inc_value` /
   `get_value`。新增 `_FullStats` 覆盖完整接口子集，与 `webhook.py._SimpleStats`
   风格一致。
4. **e2e 测试标记 `@pytest.mark.slow`**：与现有 3 个 API spider e2e 一致，
   `make check` / `make test` 默认跳过（`-m "not slow"`），`make cov` 也跳过。
   需要时显式 `uv run pytest -m slow` 运行。覆盖率门禁基于 non-slow 用例。
5. **Webhook e2e 经 Django test client**：`client.post("/api/v1/ingest/webhook/{token}")`
   走真实 URL 路由 + 端点 + pipeline + 写表 + 审计全链路，不 mock 任何环节。
   验证响应体 / 目标表数据 / IngestLog / AuditLog / task.last_sync_at 五个维度。
6. **DB spider e2e 直接串联 spider + pipeline**：不经 Scrapy CrawlerProcess（避免
   reactor 冲突），用 `spider.start()` yield items 后手动喂给 `_run_pipelines`
   串联的三个 pipeline。与顶部 `TestApiIngestE2E` 风格一致。
7. **增量 e2e 覆盖首次全量与增量过滤**：首次全量（无 `__last_sync_at__`）验证
   1970-01-01 兜底；增量过滤（`last_sync_at=2026-01-15`）验证仅 bob/carol 被拉取。
8. **changelog 按阶段组织**：v0.1.0 从「项目初始化」单项扩展为 P1~P8 完整清单，
   P8 按 Q1~Q5 子项展开，与 req-03 需求清单结构对应。
9. **external-api-guide 错误码表扩展**：404/400/409/500 行补充 webhook 相关场景，
   避免用户在「错误码汇总」与「Webhook 被动接收」两节之间来回查找。

## 代码实现情况

### external-api-guide.rst 新增「Webhook 被动接收」章节结构

1. **端点说明**：公开端点（`auth=None`），token 自身即鉴权，
   `secrets.token_urlsafe(32)` 生成。
2. **请求示例**：HTTP POST + `Idempotency-Key` 头 + JSON 数组 payload。
3. **payload 格式**：JSON 数组 / JSON 对象两种，空数组/非对象返回 400。
4. **响应示例**：`task_id` / `log_id` / `rows_read` / `rows_written` /
   `rows_skipped` / `quality_score`。
5. **处理流程 6 步**：token 鉴权 → payload 解析 → 幂等检查 → 令牌桶限流 →
   同步执行 pipeline 链 → 审计。
6. **错误码**：400/404/409/429/500 五类。
7. **约束 4 项**：不依赖 Scrapy 引擎 / 任务级凭证 / 质量分写回 / last_sync_at 更新。

### test_ingest_e2e.py 新增 6 用例

- `TestDatabaseIngestE2E.test_db_spider_full_flow`：源 SQLite users 表 3 行 →
  SQL 查询 → items → 三 pipeline → 目标 out 表 3 行，验证 rows_written=3 与行数据。
- `TestDatabaseIngestE2E.test_db_spider_with_cleaning_drop`：配置 on_missing skip
  清洗规则，覆盖一条 name 为空 → 仅 2 行写入（id=1,3），验证清洗丢弃生效。
- `TestWebhookReceiveE2E.test_webhook_http_full_flow`：POST 2 行 payload →
  验证响应体 / 目标表 2 行 / IngestLog SUCCESS / AuditLog WEBHOOK_RECEIVE /
  task.last_sync_at 更新，五维度全链路验证。
- `TestWebhookReceiveE2E.test_webhook_dict_payload_wrapped`：POST dict payload →
  包装为单元素列表 → 1 行写入目标表。
- `TestIncrementalE2E.test_db_timestamp_incremental_flow`：DB_TIMESTAMP 策略 +
  `__last_sync_at__=2026-01-15` → 仅 bob(02-01)/carol(03-01) 2 行被 yield 与写入。
- `TestIncrementalE2E.test_db_timestamp_first_run_full_pull`：首次执行（无
  `__last_sync_at__`）→ 注入 1970-01-01 全量拉取 3 行。

## 整合优化情况

- 无新重复代码。`_FullStats` 与 `webhook.py._SimpleStats` 接口一致但独立实现，
  避免跨模块导入测试辅助类。
- `_run_pipelines` 辅助函数封装三 pipeline 串联逻辑，6 个 e2e 用例复用，
  消除重复的 open/process/close 样板代码。
- `except Exception` 捕获 `DropItem` 等清洗丢弃（ruff 未启用 BLE001 规则，
  移除 `# noqa: BLE001` 避免 RUF100 误报）。

## 测试验证结果

### 端到端测试（9 用例全部通过）

```
uv run pytest tests/test_ingest_e2e.py -v
  TestDatabaseIngestE2E::test_db_spider_full_flow PASSED
  TestDatabaseIngestE2E::test_db_spider_with_cleaning_drop PASSED
  TestWebhookReceiveE2E::test_webhook_http_full_flow PASSED
  TestWebhookReceiveE2E::test_webhook_dict_payload_wrapped PASSED
  TestIncrementalE2E::test_db_timestamp_incremental_flow PASSED
  TestIncrementalE2E::test_db_timestamp_first_run_full_pull PASSED
  TestApiIngestE2E::test_full_flow_upsert PASSED
  TestApiIngestE2E::test_upsert_updates_existing PASSED
  TestApiIngestE2E::test_skip_strategy_skips_conflicts PASSED
  9 passed in 1.85s
```

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 已格式化
uv run pyrefly check                          # 0 errors
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1933 passed, 21 deselected, 54 warnings in 88.57s
  TOTAL 9228 stmts, 339 miss, 1862 branch, 132 brpart, 95.43%
```

覆盖率 95.43%（≥95% 门禁），与 iter-54 基线持平。新增 6 个 slow 用例不参与
覆盖率统计（`-m "not slow"` 跳过），non-slow 用例数 1933 与 iter-54 一致。

测试数 1933 + 21 slow = 1954 总用例（iter-54 为 1933 + 15 slow = 1948，
新增 6 slow 用例）。

## 遗留事项

- P8（数据清洗与质量提升）全部交付完毕（item 36-49 全部闭环），P8 里程碑达成。
- Webhook 端点可考虑加 IP 白名单（当前仅 token 鉴权 + 限流，iter-54 遗留）。
- DatabaseIngestSpider 可考虑加查询超时（当前依赖 SQLAlchemy 默认超时，iter-54 遗留）。
- 项目当前 P0-P8 全部完成，后续若无新需求，可进入收尾或等待新 req。

## 下一轮计划

- req-03 P8 全部闭环。若无新增 req 或遗留事项的明确指示，进入项目收尾阶段：
  输出总结（交付物、关键决策、遗留事项）→ 等待用户确认是否推进新需求。
- 若用户确认推进 iter-54 遗留（Webhook IP 白名单 / DB 查询超时），则按六步闭环
  启动 iter-56。
