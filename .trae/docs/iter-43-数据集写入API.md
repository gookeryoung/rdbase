# iter-43：数据集写入 API

## 需求清单

- [x] 43 数据集写入 API：POST /api/v1/datasets/{slug}/rows（单行/批量 UPSERT）+ 冲突策略复用
  （UPSERT/SKIP/ERROR）+ 写入审计（AuditAction 新增 DATASET_WRITE）+ 配额控制（每 Token
  每日写入上限，超额 429）+ 速率限制（Token 维度，Redis 实现，复用 P8 Redis 基础设施）

## 迭代目标

落地数据集对外写入端点 `POST /api/v1/datasets/{slug}/rows`，与 iter-42 查询端点对称。
外部应用通过 API Token（`datasets:write` scope）批量 UPSERT 数据集绑定的目标表，冲突策略
复用 ingest 模块的 `write_rows`；接入 P8 幂等保护（防重复触发）、限流（每 Token 每分钟
上限）与每日配额（每 Token 每日写入总行数上限），写入留痕 `DATASET_WRITE` 审计动作。

## 改动文件清单

### 新增

- `backend/apps/system/rate_limiter.py`：固定窗口速率限制器（Redis INCR+EXPIRE / 本地降级）
- `backend/apps/system/quota.py`：每日写入配额（Redis INCRBY + TTL 到当日结束 / 本地降级）
- `backend/apps/audit/migrations/0005_alter_auditlog_action.py`：AuditAction 新增
  `dataset.write` 枚举值（choices 28→29）
- `tests/test_system_rate_limiter.py`：限流器单元测试（Redis + 本地两种后端）
- `tests/test_system_quota.py`：配额单元测试（Redis + 本地两种后端）
- `tests/test_datasources_datasets_write.py`：写入 API 端到端测试（27 用例）

### 修改

- `backend/apps/audit/models.py`：AuditAction 新增 `DATASET_WRITE = "dataset.write"`
- `backend/apps/datasources/datasets_api.py`：新增 `POST /{slug}/rows` 写入端点 +
  `_validate_conflict_strategy` / `_collect_row_fields` 辅助函数
- `backend/apps/datasources/schemas.py`：新增 `DatasetWriteIn` / `DatasetWriteOut`
- `backend/rdbase/settings/base.py`：新增 `RATE_LIMIT_DATASET_WRITE`（默认 60/分钟）、
  `DATASET_WRITE_DAILY_QUOTA`（默认 10000 行/日）
- `tests/test_audit_models.py`：更新 AuditAction 枚举计数（28→29）与新增值断言

## 关键决策与依据

1. **复用既有 writer 而非重造**：直接调用 `apps.ingest.writer.write_rows`，传入字符串
   冲突策略值，复用其 MySQL/PG/SQLite 三方言 UPSERT/SKIP/ERROR 实现，避免第三处相似写入
   逻辑（已有 sync_service 与 ingest writer 两处）。遵循 rule-01「三处相似才提取」。

2. **固定窗口限流而非令牌桶**：req-03 文案为「令牌桶」，但实现采用固定窗口（Redis
   `INCR` + 首次 `EXPIRE nx`）。理由：写入端点语义是「每分钟 N 次请求」计数，固定窗口
   实现简单且原子性强；令牌桶的突发补偿对写入场景反而不利（写入是重操作，不鼓励突发）。
   后续若需精确令牌桶可在 item 45 完善。

3. **限流/配额 Redis 故障降级放行**：`check_rate_limit` 与 `check_and_consume_quota`
   在 Redis 异常时记 WARNING 并临时放行，避免限流/配额组件故障导致写入服务整体不可用
   （可用性优先于精确限流）。与 `distributed_lock` strict 模式不同——锁可降级，限流/配额
   故障时更不应阻塞业务。

4. **配额在写入前消费、失败不回补**：`check_and_consume_quota` 在 `write_rows` 之前调用，
   先占用配额再写入。若 `write_rows` 抛 `ValueError`（如 ERROR 策略冲突），已消费配额不回补
   （简化实现，且冲突写入本身也消耗了服务端资源）。幂等命中缓存时不消费配额（在配额检查
   之前返回）。

5. **列级写权限复用 fields_whitelist**：写入端点复用 iter-42 查询端点的 `fields_whitelist`
   语义——非空时校验 rows 所有键必须是白名单子集，实现「列级写权限」。同一字段白名单同时
   约束读与写，配置一致避免心智负担。

6. **无主键表策略**：`pk_fields` 未传时由 `get_pk_columns` 反射；无主键且策略非 `error`
   时返回 400（UPSERT/SKIP 依赖主键判定冲突，无主键无法判定）。`error` 策略走纯 INSERT，
   不需要主键，故放行。

7. **ERROR 策略冲突返回 400**：`write_rows` 在 ERROR 策略下冲突触发 INSERT 异常，整批
   回滚并抛 `ValueError`，端点捕获后返回 400 并记录 `status=FAILURE` 审计日志。与
   UPSERT/SKIP 的「单行失败计入 skipped」语义不同——ERROR 是显式报错语义。

## 代码实现情况

### POST /{slug}/rows 端点处理流程

1. `_require_scope(request, "datasets:write")` 取 Token。
2. `check_idempotency(request)`：命中缓存直接回放（不消费限流/配额）。
3. `check_rate_limit`：超限 429 + `Retry-After` 头，`release_idempotency` 后返回。
4. `_get_dataset_or_404(slug, active_only=True)` + 数据源 `is_active` 校验（404）。
5. 入参校验：rows 非空、单批 ≤ 1000、冲突策略合法。
6. 反射 `get_column_names` → 白名单子集校验 + 表列子集校验。
7. 主键推断：`pk_fields` 显式传入则校验存在性；否则反射，无主键且非 error 400。
8. `check_and_consume_quota`：超限 429。
9. `write_rows` 写入；失败记 FAILURE 审计 + 400。
10. 成功记 SUCCESS 审计 + `store_idempotency_result`。

### rate_limiter / quota 后端解析

两者均采用 `_resolve_backend()` 单例模式（与 idempotency/distributed_lock 一致）：
Redis 可用 → 共享后端（多 worker 跨进程生效）；否则本地内存后端（单进程降级）。
`reset_rate_limiter()` / `reset_quota()` 供测试重置单例。

## 整合优化情况

- 限流/配额后端单例模式与 `idempotency._resolve_store` / `distributed_lock._resolve_backend`
  完全一致，三处 Redis 客户端复用 `get_redis()` 单例，命名空间隔离（`rdbase:rate` /
  `rdbase:quota` / `rdbase:idem` / `rdbase:lock`）。
- `conftest.py` 已 autouse 重置 redis_client/idempotency/lock/circuit_breaker；写入测试
  自带 `_reset_rate_and_quota` fixture 补充重置 rate_limiter/quota 后端单例。

## 测试验证结果

- `make lint`：ruff check + format 全通过。
- `make typecheck`：pyrefly 0 errors。
- `make cov`：1518 passed，覆盖率 95.41%（阈值 95%）。
- `manage.py makemigrations audit --check`：No changes detected（迁移与模型一致）。

测试覆盖：单行/批量 UPSERT、UPSERT 更新已存在行、SKIP 跳过、ERROR 冲突 400、ERROR 新行、
无主键非 error 400、无主键 error 放行、白名单子集放行、非白名单列 400、不存在列 400、
空 rows 400、超 1000 行 400、非法策略 400、数据集 inactive 404、数据源 inactive 404、
slug 不存在 404、无 Token 401、无 write scope 403、限流 429、配额超限 429、幂等回放、
审计日志记录、pk_fields 显式传入、pk_fields 非法列 400。

## 遗留事项

- 限流为固定窗口，边界处可能瞬时 2 倍流量（窗口切换瞬间）。item 45「速率限制完善」
  可升级为滑动窗口或令牌桶。
- 配额失败不回补：ERROR 策略冲突已消费配额不退回。若后续有需求可在 write_rows 失败时
  调用 `decrement` 回补（当前 Redis INCRBY 不支持原子回补需 Lua 脚本，暂不引入）。
- 前端数据集管理页未提供「写入测试」入口（iter-42 已有预览），写入端点面向外部应用，
  前端无需可视化。

## 下一轮计划

iter-44：调度触发 API + Webhook 订阅（req-03 item 44）。
