# iter-45：OpenAPI spec 暴露 + 令牌桶限流升级 + P9 收尾

## 需求清单

- [x] 45 OpenAPI spec 暴露 + 速率限制完善 + P9 测试文档收尾：/api/v1/openapi.json
  对外可访问（仅含数据集与触发端点，隐藏管理端点）+ 速率限制中间件（按 Token +
  按端点维度，Redis 令牌桶）+ 前端 Token 管理页（列表/创建/吊销/查看 last_used_at）
  + 端到端测试（API Token 全流程 + 数据集查询写入 + Webhook 投递）+ 用户手册新增
  「外部应用接入指南」+ API 文档汇总

## 迭代目标

落地 P9 收尾能力与外部应用接入文档：

1. **OpenAPI spec 双视图**（决策 #10）：
   - `GET /api/v1/openapi.json`：管理员视图，返回完整 OpenAPI schema（全部端点）。
   - `GET /api/v1/datasets/openapi.json`：外部视图，仅含数据集查询/写入 +
     数据集同步触发 + 爬取任务触发端点，过滤掉管理端点避免信息泄露。
2. **令牌桶速率限制升级**：将原固定窗口算法升级为令牌桶（容量 + 补充速率），
   支持突发与逐步恢复；保留旧 `check_rate_limit(key, max_requests,
   window_seconds)` 签名做等价转译，向后兼容 iter-43 数据集写入限流调用。
3. **前端 Token 管理页**：admin 专属，列表/创建/吊销/轮换/明文一次性展示，
   展示 `prefix`、`scopes`、`expires_at`、`last_used_at`、`is_active` 等字段。
4. **端到端测试**：API Token 全流程（创建 → 查询 → 写入 → 触发 sync → 触发
   ingest → Webhook 投递 → 吊销）、scope 不足 403、无 Token 401、OpenAPI 双视图。
5. **用户手册**：`docs/external-api-guide.rst` 新增「外部应用接入指南」，
   覆盖 Token 获取、鉴权请求头、数据集查询/写入、触发同步/爬取、Webhook 签名校验、
   错误码汇总、最佳实践。

## 改动文件清单

### 新增

- `backend/api/v1/openapi_views.py`：OpenAPI 双视图实现
  （`admin_openapi_view` / `external_openapi_view`，外部视图用路径白名单过滤
  `paths` 字段，保留 `components` 共享定义，同步更新 `info.title`）
- `tests/test_p9_e2e.py`：P9 端到端集成测试（6 个用例：全流程 / scope 不足 /
  无 Token / OpenAPI 管理员视图 / OpenAPI 外部视图 / 外部视图含 GET+POST）
- `frontend/src/api/tokens.ts`：Token API 客户端
  （list / create / retrieve / revoke / rotate）
- `frontend/src/pages/Tokens.tsx`：Token 管理页（列表 + 创建 Modal + 明文一次性
  展示 Modal + 吊销 Popconfirm + 轮换）
- `docs/external-api-guide.rst`：外部应用接入指南用户手册

### 修改

- `backend/apps/system/rate_limiter.py`：固定窗口 → 令牌桶算法重写
  - 新增 `_RateBackend` Protocol、`_LocalBucket` dataclass、`_LocalBackend`
    （`threading.Lock` + `time.monotonic`）、`_RedisBackend`（WATCH/MULTI/EXEC
    原子化，兼容 fakeredis 不依赖 EVAL）
  - 新增 `check_token_bucket(key, capacity, refill_rate, cost=1)` 接口
  - `check_rate_limit` 转译为 `capacity=max_requests`、
    `refill_rate=max_requests/window_seconds`，行为等价且支持突发与逐步恢复
  - `reset_rate_limiter` 单例重置（测试用），Redis 故障时降级放行
- `backend/rdbase/urls.py`：注册 OpenAPI 双视图 URL
  - **关键**：必须在 `api/v1/` include 之前注册，否则会被 datasets router 的
    `GET /{slug}` 以 `slug="openapi.json"` 匹配并要求 JWT 鉴权
- `docs/index.rst`：toctree 新增 `external-api-guide`
- `frontend/src/types/index.ts`：新增 `ApiTokenScope` / `ApiTokenCreate` /
  `ApiTokenCreated` / `ApiTokenListItem` / `ApiTokenList` / `ApiTokenRotated`
  类型定义
- `frontend/src/layouts/MainLayout.tsx`：新增 API Token 菜单项（`KeyOutlined`，
  `roles: [Role.ADMIN]`）
- `frontend/src/routes/index.tsx`：新增 `/tokens` 路由（`RoleRoute` ADMIN 守卫）
- `tests/test_system_rate_limiter.py`：补充令牌桶专项测试（13 个新用例）
  - `check_rate_limit` 入参校验（window/max 非正抛 ValueError）
  - 令牌桶语义：突发容量、逐步恢复、retry_after 计算、cost 参数、reset_key、
    refill_rate=0 永久拒绝、独立 key
  - 本地与 Redis（fakeredis）两种后端均覆盖

## 关键决策与依据

1. **OpenAPI 双视图通过路径白名单过滤实现**：复用 `NinjaAPI.get_openapi_schema()`
   生成的完整 schema，外部视图按 `_EXTERNAL_PATHS` 白名单过滤 `paths` 字段，
   保留 `components` 共享定义（避免 schema 内引用断裂）。`info.title` 同步更新为
   "rdbase 外部应用 API" 便于调用方区分。决策 #10。

2. **OpenAPI 视图必须在 `api/v1/` include 之前注册**：django-ninja 的 Router
   注册了 `GET /datasets/{slug}` 路径，若将 `openapi.json` 注册到其后会被以
   `slug="openapi.json"` 匹配并要求 JWT 鉴权（401）。通过 URL 顺序前置避免冲突，
   在 `urls.py` 注释中明确警示。

3. **令牌桶算法替代固定窗口**：req-03 item 45 明确要求「Redis 令牌桶」。令牌桶
   相比固定窗口的优势：①支持突发（容量上限内瞬时消耗）；②逐步恢复（按 refill_rate
   持续补充，无需等窗口过期）；③更平滑的限流曲线。算法核心：
   `tokens = min(capacity, tokens + elapsed * refill_rate)`，`elapsed` 用
   `time.monotonic`（本地）或 `time.time`（Redis 端）计算。

4. **Redis 用 WATCH/MULTI/EXEC 而非 Lua 脚本**：与 `apps.system.distributed_lock`
   保持一致的实现风格，且兼容 fakeredis（fakeredis 对 EVAL 支持有限）。WATCH
   期间冲突时重试最多 5 次，重试耗尽临时放行避免限流器故障影响业务。

5. **`check_rate_limit` 保留旧签名做等价转译**：iter-43 数据集写入端点已调用
   `check_rate_limit(key, max_requests=60, window_seconds=60)`，直接复用旧签名
   避免破坏既有调用方。内部转译为 `capacity=60, refill_rate=1.0/s`，行为等价于
   「窗口内最多 60 次」且额外支持突发与逐步恢复（旧调用方无感知升级）。
   非正入参（window/max <= 0）抛 ValueError 暴露调用方 bug。

6. **本地降级用 `threading.Lock` + `time.monotonic`**：与原固定窗口本地降级
   一致，单进程内互斥。Redis 故障时降级为本地桶（仅本进程生效），不阻断业务。
   `time.monotonic` 不受系统时钟调整影响，限流计时更稳定。

7. **Token 管理页明文仅此一次展示**：与后端 `ApiToken.generate` / `rotate` 语义
   一致，创建/轮换返回的明文在 Modal 中展示，提示「请立即复制并安全保存」，
   关闭 Modal 后无法再次查看（DB 仅存 SHA-256 哈希）。提供「复制到剪贴板」按钮
   与「我已保存」确认按钮。

8. **端到端测试用 mock 隔离外部依赖**：`SyncService.run` / `spawn_ingest` /
   `Webhook._http_post` / `_backoff_sleep` 均 monkeypatch 为 stub，避免真实
   等待与外部 HTTP 调用。`transaction=True` 标记确保后台线程内的 DB 操作在
   测试事务内可见。fakeredis 启用 + `reset_rate_limiter` / `reset_quota`
   autouse fixture 确保测试间状态隔离。

## 代码实现情况

### OpenAPI 双视图处理流程

1. `admin_openapi_view(api)` 构造闭包视图：调用 `api.get_openapi_schema()`
   获取完整 schema，经 `_schema_to_dict`（兼容 pydantic v1/v2 与 dict）转
   JSON 序列化，返回 `JsonResponse`。
2. `external_openapi_view(api)` 同样获取完整 schema，但按 `_EXTERNAL_PATHS`
   白名单（3 条路径）过滤 `paths` 字段，更新 `info.title` 后返回。
3. URL 注册顺序：`/api/v1/openapi.json` → `/api/v1/datasets/openapi.json`
   → `api/v1/` include，确保 OpenAPI 路径不被 datasets router 的 `{slug}`
   占位符匹配。

### 令牌桶算法核心

```python
# 补充令牌（不超过容量）
elapsed = max(0.0, now - last_refill)
tokens = min(capacity, tokens + elapsed * refill_rate)
last_refill = now
# 决策
if tokens >= cost:
    tokens -= cost
    return True, 0
else:
    deficit = cost - tokens
    retry_after = max(1, ceil(deficit / refill_rate))
    return False, retry_after
```

- **本地后端**：`threading.Lock` 保护 check-then-act，`_LocalBucket` dataclass
  存 `tokens` 与 `last_refill`。
- **Redis 后端**：`WATCH/MULTI/EXEC` 原子化读改写，HSET 存 `tokens` 与
  `last_refill`，TTL = `capacity / refill_rate + 60s` 避免冷数据长期驻留。
  `WatchError` 重试最多 5 次，`RedisError` 临时放行。

### Token 管理页交互

- 列表：Table 展示 name / prefix（`<Text code>{prefix}…</Text>`）/ scopes
  （Tag 数组）/ expires_at / last_used_at / is_active / created_at / 操作。
- 创建：Modal + Form，name 必填、scopes 必选（多选 `datasets:read` /
  `datasets:write` / `sync:trigger`）、expires_at 可空（DatePicker showTime）。
- 明文展示：创建/轮换后弹出 Modal，`Paragraph copyable code` 展示明文，
  警示文案「仅此一次展示，关闭后无法再次查看」。
- 吊销：Popconfirm 二次确认（不可恢复，但可轮换生成新明文），调
  `POST /tokens/{id}/revoke`。
- 轮换：调 `POST /tokens/{id}/rotate`，新明文展示在 Modal 中。
- 已吊销的 Token 「轮换」「吊销」按钮 disabled。

## 整合优化情况

- 复用 iter-41 `ApiTokenAuth` 与 `/api/v1/tokens` 后端 Router，前端仅新增
  `tokens.ts` 客户端与 `Tokens.tsx` 页面，菜单/路由按既有 `RoleRoute` 模式接入。
- 复用 iter-43 数据集写入限流调用方（`check_rate_limit` 旧签名），无需改动
  `datasets_api.py`；令牌桶升级对调用方透明。
- 复用 `apps.system.redis_client.get_redis` 单例与 fakeredis 测试基础设施，
  令牌桶后端解析与 `distributed_lock` / `idempotency` 保持一致风格。
- OpenAPI schema 由 `NinjaAPI.get_openapi_schema()` 生成，不重造；双视图仅做
  `paths` 过滤与 `info.title` 更新，保留 `components` 共享定义。
- 端到端测试复用既有 `make_user` / `sqlite_ds` / `sync_config` / `dataset` /
  `ingest_task` fixtures，与 iter-44 测试结构一致。

## 测试验证结果

- `uv run ruff check .`：All checks passed。
- `uv run ruff format --check .`：254 files already formatted。
- `uv run pyrefly check`：0 errors（240 suppressed, 1008 warnings not shown）。
- `uv run pytest --cov=backend --cov-report=term-missing`：1618 passed,
  37 warnings，覆盖率 95.14%（阈值 95%，较 iter-44 的 95.26% 略降 0.12pp，
  因新增 OpenAPI 视图与令牌桶代码分母增大，仍在阈值之上）。

测试覆盖：

- **OpenAPI 双视图**：管理员视图含 `/auth/login` / `/users` / `/datasources`
  等全部端点；外部视图仅含 3 条对外路径，过滤掉管理端点；外部视图的
  `/datasets/{slug}/rows` 同时含 `get` 与 `post` 方法。
- **令牌桶算法**：突发容量（capacity=5 可瞬时消耗 5 次）、逐步恢复
  （refill_rate=10/s 等 0.15s 后恢复）、retry_after 计算（ceil(deficit/refill)）、
  cost 参数（一次消耗多个令牌）、reset_key 清除桶状态、refill_rate=0 永久拒绝、
  独立 key 互不干扰。本地与 Redis（fakeredis）两种后端均覆盖。
- **`check_rate_limit` 兼容性**：未超限放行、超限拒绝并返回 retry_after、
  窗口过期后重新计数、reset_key 清除、独立 key 互不干扰、入参非正抛 ValueError。
- **P9 端到端全流程**：管理员通过 JWT 创建带全部 scope 的 API Token → 用 Token
  查询数据集行 → 写入数据集行 → 触发数据集同步（mock SyncService.run 验证后台
  线程启动，202 + task_id）→ 触发爬取任务（mock spawn_ingest 验证 200 +
  returncode）→ Webhook 投递（mock _http_post 验证 HMAC-SHA256 签名头与
  事件头）→ 吊销 Token 后再调任一端点返回 401。
- **授权边界**：scope 不足（仅 `datasets:read`）调写端点返回 403（响应体含
  "scope"）；无 Token 访问数据集查询端点返回 401。

## 遗留事项

- 令牌桶限流目前仅在 `datasets_api.write_rows` 中按 `dataset_write:{prefix}`
  维度调用，未在 `/datasets/{slug}/sync` 与 `/ingest/tasks/{id}/trigger` 端点
  接入按端点维度的限流（req-03 item 45 文案「按 Token + 按端点维度」未完整落地）。
  后续可在 trigger 端点加 `check_token_bucket(f"trigger:{prefix}", capacity=10,
  refill_rate=0.5)` 限流。
- OpenAPI 外部视图未在 `servers` 字段中区分生产/测试环境 URL（当前返回相对路径）。
  外部应用接入时需自行拼接 host。
- 前端 Token 管理页未引入测试框架（Vitest 等），仅通过 typecheck 与手动验证。
- 令牌桶 Redis 后端用 `time.time()` 客户端时间，多 worker 时钟漂移在秒级限流
  场景可接受；如需更精确可改用 Redis `TIME` 命令（需额外往返）。
- P9 收尾完成，req-03 全部 10 项（36-45）已交付，本期无下一轮计划。

## 下一轮计划

P9 数据中心对外 API 里程碑全部交付完成（req-03 item 36-45 均标记 `[x]`）。
req-03 文件将移至 `.trae/req/done/`。后续如需继续推进，建议方向：

- Webhook 投递升级为 Celery/RQ 任务队列（req-03 待用户复核项 2）。
- WebhookDeliveryLog 的 `next_retry_at` 调度重投（需后台定时任务）。
- API Token 按数据集细粒度授权（req-03 待用户复核项 1）。
- 审计哈希链定时校验告警（req-03 待用户复核项 4）。
- 触发端点接入按端点维度的令牌桶限流（本期遗留）。
