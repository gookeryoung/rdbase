# iter-47：触发端点接入令牌桶限流

## 需求清单

- [x] 47 触发端点（sync trigger + ingest trigger）接入按 Token 维度的令牌桶限流：
  iter-45 遗留的 P9 收尾事项，两个外部触发端点缺少速率限制保护，可能被高频
  调用拖垮后台同步/爬取资源。

## 迭代目标

为 `POST /datasets/{slug}/sync` 与 `POST /ingest/tasks/{id}/trigger` 两个外部
触发端点接入令牌桶限流，与 `write_dataset_rows` 的「scope → 幂等 → 限流 → 业务」
顺序保持一致，防止外部应用高频触发耗尽系统资源。

## 改动文件清单

### 修改

- `backend/rdbase/settings/base.py`：新增 `RATE_LIMIT_TRIGGER_CAPACITY`（默认 10）
  与 `RATE_LIMIT_TRIGGER_REFILL_RATE`（默认 0.5），令牌桶语义：突发 10 次、
  每 2 秒恢复 1 次。
- `backend/apps/datasources/datasets_api.py`：
  - import 补充 `check_token_bucket`。
  - `trigger_dataset_sync` 在幂等检查之后、分布式锁之前插入令牌桶限流；
    `token` 变量从原 L765 提前到限流之前。
  - 文档字符串补充限流步骤（第 5 步）。
- `backend/apps/ingest/api.py`：
  - 新增 `from django.conf import settings` 与
    `from apps.system.rate_limiter import check_token_bucket`。
  - `trigger_task` 在幂等检查之后、分布式锁之前插入令牌桶限流。
  - 文档字符串补充限流步骤（第 4 步）。
- `tests/test_p9_e2e.py`：
  - 新增 `_FakeLock` 辅助类（限流测试隔离锁逻辑）。
  - 新增 3 个测试：`test_sync_trigger_rate_limited_429`、
    `test_ingest_trigger_rate_limited_429`、
    `test_trigger_rate_limit_independent_per_token`。
- `docs/external-api-guide.rst`：触发同步 / 触发爬取章节补充速率限制说明。

## 关键决策与依据

1. **限流 key 设计**：`trigger:{token.prefix}`，sync trigger 与 ingest trigger
   共享一个桶。依据：两个端点都是「触发执行」语义，对外应视为同一类高频操作；
   共享桶防止调用方通过交替调两个端点绕过限流。按 Token 维度隔离，不同 Token
   互不影响。

2. **用 `check_token_bucket` 而非 `check_rate_limit`**：`check_rate_limit` 是
   兼容旧接口（固定窗口），内部转调 `check_token_bucket`。触发端点是新接入，
   直接用令牌桶语义展示 capacity/refill_rate 参数，与 iter-45 限流器升级方向
   一致。写入端点保留 `check_rate_limit` 不变（已有测试与文档）。

3. **插入位置：幂等之后、锁之前**：与 `write_dataset_rows`（L567-581）的
   scope → 幂等 → 限流 → 业务 顺序一致。幂等命中（回放缓存）不消耗限流配额，
   避免重试合法请求被限流。

4. **限流失败处理**：`release_idempotency(request)` 释放幂等占位 +
   `JsonResponse(status=429)` + `Retry-After` 头，与写入端点限流失败处理一致。

5. **sync trigger 测试 mock get_lock**：sync trigger 异步启动后台线程，后台线程
   在 finally 中释放分布式锁。测试中 mock `SyncService.run` 为空操作后，后台
   线程仍异步执行，连续调用可能因锁未释放返回 409 而非 202，干扰限流测试。
   用 `_FakeLock`（始终可获取、release 空操作）隔离锁逻辑，专注验证限流行为。

6. **测试用 `refill_rate=0.0`**：禁止令牌补充，确保测试运行期间令牌数只减不增，
   3 次调用（容量 2）稳定触发 429。`_calc_retry_after` 对 `refill_rate <= 0`
   返回 1，不触发除零。

## 代码实现情况

### settings 配置

```python
RATE_LIMIT_TRIGGER_CAPACITY: int = 10
RATE_LIMIT_TRIGGER_REFILL_RATE: float = 0.5
```

### 限流逻辑（两个端点一致）

```python
rate_key = f"trigger:{token.prefix}"
allowed, retry_after = check_token_bucket(
    rate_key,
    capacity=settings.RATE_LIMIT_TRIGGER_CAPACITY,
    refill_rate=settings.RATE_LIMIT_TRIGGER_REFILL_RATE,
)
if not allowed:
    release_idempotency(request)
    resp = JsonResponse(
        {"detail": f"触发请求过于频繁，请 {retry_after} 秒后重试"},
        status=429,
    )
    resp["Retry-After"] = str(retry_after)
    return resp
```

### 测试覆盖

- `test_sync_trigger_rate_limited_429`：容量 2，连续 3 次调 sync trigger，
  前 2 次 202、第 3 次 429 + Retry-After + detail 含「频繁」。
- `test_ingest_trigger_rate_limited_429`：容量 2，连续 3 次调 ingest trigger，
  前 2 次 200、第 3 次 429 + Retry-After。
- `test_trigger_rate_limit_independent_per_token`：Token A 耗尽配额后 429，
  Token B 不受影响仍 200，验证按 Token 维度独立桶。

## 整合优化情况

- 复用 `check_token_bucket` 既有接口，不新增限流后端代码。
- 限流失败响应格式与 `write_dataset_rows` 完全一致（detail 文案、429 状态、
  Retry-After 头），保持对外错误处理一致性。
- 测试复用 `_fake_spawn_success`、`_make_token`、`_token_client` 等既有辅助。
- `_FakeLock` 仅在限流测试中使用，不影响既有锁测试。

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed。
- `uv run ruff format --check backend tests`：227 files already formatted。
- `uv run pyrefly check`：0 errors（244 suppressed, 1023 warnings not shown）。
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：
  1612 passed, 15 deselected，覆盖率 95.21%（≥ 95% 阈值通过）。
- 前端 `bun run typecheck`（tsc --noEmit）：通过。

## 遗留事项

- iter-45 遗留的「WebhookDeliveryLog 重投调度」「API Token 按数据集细粒度授权」
  「审计哈希链定时校验」等待用户复核是否推进。
- 触发端点限流配置为全局静态 settings，未提供按 Token/按数据集动态调整能力；
  后续若需差异化限流可扩展 SystemSetting 动态配置。

## 下一轮计划

本期触发端点限流收尾完成，无下一轮计划。如需推进 iter-45 遗留的对外 API
增强方向（Webhook 重投、Token 细粒度授权、审计哈希链校验），待用户确认后
启动新迭代。
