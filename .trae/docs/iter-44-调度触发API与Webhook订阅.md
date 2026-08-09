# iter-44：调度触发 API + Webhook 订阅

## 需求清单

- [x] 44 调度触发 API + Webhook 订阅：POST /api/v1/datasets/{slug}/sync（触发绑定 sync
  配置）+ POST /api/v1/ingest/tasks/{id}/trigger（外部触发爬取，复用现有 run 逻辑但走
  异步队列；接入 P8 分布式锁防并发、P8 幂等防重复触发）+ WebhookSubscription 模型
  （事件类型/目标 URL/secret/启用）+ 事件投递器（sync/ingest 完成后异步 POST，
  HMAC-SHA256 签名，失败指数退避重试最多 5 次）+ 前端订阅管理页

## 迭代目标

落地 P9 对外触发能力与 Webhook 事件订阅：

1. **数据集同步触发端点** `POST /api/v1/datasets/{slug}/sync`：外部应用按 slug 触发
   绑定的 sync 配置执行，API Token + `sync:trigger` scope 鉴权，异步后台线程执行
   并立即返回 `task_id`，接入幂等保护与分布式锁。
2. **爬取任务触发端点** `POST /api/v1/ingest/tasks/{id}/trigger`：与内部 `/run` 对应，
   供外部应用通过 API Token 触发爬取，复用 `spawn_ingest` 子进程执行逻辑。
3. **WebhookSubscription / WebhookDeliveryLog 模型**：订阅配置（URL/secret/events）
   与投递日志，事件类型 `sync.completed` / `ingest.completed`。
4. **Webhook 投递器**：sync/ingest 完成后异步分发事件，HMAC-SHA256 签名头，
   失败指数退避重试最多 5 次（1/2/4/8/16s），每次投递写一条 DeliveryLog。
5. **Webhook 管理 API**：CRUD + 投递日志查询，全部 `JWTAuth` + `require_admin`。
6. **前端订阅管理页**：admin 专属，列表/新增/编辑/删除/查看投递日志。

## 改动文件清单

### 新增

- `backend/apps/webhook/__init__.py` / `apps.py` / `admin.py`：Webhook app 骨架
- `backend/apps/webhook/models.py`：`WebhookSubscription`、`WebhookDeliveryLog` 模型
  + `SigningAlgorithm`、`DeliveryStatus` 枚举
- `backend/apps/webhook/schemas.py`：订阅/投递日志的 Pydantic Schema
- `backend/apps/webhook/api.py`：Webhook CRUD + 投递日志查询 Router（6 端点）
- `backend/apps/webhook/deliverer.py`：事件投递器（HMAC-SHA256 + 指数退避重试）
- `backend/apps/webhook/migrations/0001_initial.py`：Webhook 表迁移
- `backend/apps/datasources/migrations/0003_dataset_sync_config.py`：Dataset 新增
  `sync_config` 外键字段
- `tests/test_webhook_models.py`：Webhook 模型单元测试（含枚举、`is_subscribed`、
  名称唯一约束、索引、`__str__`）
- `tests/test_webhook_deliverer.py`：投递器测试（签名计算、重试退避、状态码分流、
  payload/timing 日志、订阅匹配、异常容错、`wait=True` 同步等待）
- `tests/test_webhook_api.py`：Webhook 管理 API 端到端测试（CRUD 权限、参数校验、
  投递日志查询过滤/limit/404）
- `tests/test_datasources_datasets_sync.py`：数据集同步触发端点测试（scope 校验、
  幂等回放、分布式锁 409、未绑定/暂停配置 400、异步执行、审计日志）
- `tests/test_ingest_trigger.py`：爬取触发端点测试（scope 校验、幂等回放、分布式锁
  409、spawn_ingest 失败 500、审计日志）
- `frontend/src/api/webhooks.ts`：Webhook API 客户端（CRUD + 投递日志查询）
- `frontend/src/pages/Webhooks.tsx`：Webhook 管理页（列表/新增/编辑/删除/投递日志抽屉）

### 修改

- `backend/apps/audit/models.py`：`AuditAction` 新增 `SYNC_TRIGGER`、`INGEST_TRIGGER`、
  `WEBHOOK_DELIVER` 三个枚举值（choices 29→32）
- `backend/apps/datasources/datasets_api.py`：新增 `POST /{slug}/sync` 公开触发端点
  + `_require_scope` 辅助函数
- `backend/apps/datasources/models.py`：`Dataset` 新增 `sync_config` 外键（可空，
  `SET_NULL`），`_dataset_to_dict` 输出 `sync_config_id`
- `backend/apps/datasources/schemas.py`：新增 `DatasetSyncTriggerOut`
- `backend/apps/datasources/migrations/0002_dataset.py`：迁移同步 `sync_config` 字段
- `backend/apps/ingest/api.py`：新增 `POST /tasks/{id}/trigger` 公开触发端点
  + `_require_scope` / `_log_to_out` 辅助函数
- `backend/apps/ingest/schemas.py`：新增 `IngestTriggerOut`
- `backend/apps/ingest/engine.py`：`execute_task` 完成后调用
  `_emit_ingest_completed_event` 分发事件（`wait=True`）
- `backend/apps/sync/sync_service.py`：`SyncService.run` 成功后调用
  `_emit_sync_completed_event` 分发事件
- `backend/api/v1/__init__.py`：注册 webhook Router（`/webhooks`）
- `backend/rdbase/settings/base.py`：`INSTALLED_APPS` 新增 `apps.webhook`
- `tests/test_audit_models.py`：更新 AuditAction 枚举计数断言（29→32）与新增值断言
- `frontend/src/types/index.ts`：新增 `WebhookSubscription`、`WebhookDeliveryLog`
  等接口；`AuditAction` 新增 `sync.trigger` / `ingest.trigger` / `webhook.deliver`
- `frontend/src/pages/AuditLogs.tsx`：`actionLabel` / `actionColor` 新增三项映射
- `frontend/src/layouts/MainLayout.tsx`：新增 Webhook 菜单项（`BellOutlined`，
  `roles: [Role.ADMIN]`）
- `frontend/src/routes/index.tsx`：新增 `/webhooks` 路由（`RoleRoute` ADMIN 守卫）

## 关键决策与依据

1. **同步触发异步、爬取触发同步**：sync 任务通常耗时秒级且无返回值，采用后台线程
   异步执行立即返回 `task_id`（202 Accepted），调用方通过 SyncLog 对账；ingest 通过
   `spawn_ingest` 子进程执行，外部调用方需要立即拿到 returncode 与 stderr 以判定
   爬取是否启动成功，故同步等待返回（200 OK）。差异源于执行语义不同。

2. **触发端点统一 `sync:trigger` scope**：req-03 文案将 sync 与 ingest 触发统一为
   `sync:trigger` scope（"调度触发"语义），不细分为 `sync:trigger` / `ingest:trigger`。
   简化外部应用授权心智——一个 scope 即可触发所有调度能力。

3. **触发端点复用 P8 幂等 + 分布式锁**：`POST /{slug}/sync` 与 `POST /tasks/{id}/trigger`
   均接入 `check_idempotency`（`Idempotency-Key` 24h 缓存）与 `get_lock`（与内部
   `/sync/configs/{id}/trigger` / `/tasks/{id}/run` 同锁名），防重复触发与并发执行。
   锁名复用确保外部触发与内部触发互斥。

4. **Webhook secret 明文存储**：与 GitHub/Stripe 等平台一致，secret 明文存储由管理面
   分配并 HTTPS 传输，供接收方校验 `X-Webhook-Signature`。不进入普通日志，列表/详情
   接口不回显 secret（仅创建/更新时输入）。后续可升级为加密存储（需密钥管理基础设施）。

5. **投递器使用标准库 urllib 而非 requests**：项目未依赖 `requests`，为单一投递功能
   引入新依赖不划算。`urllib.request` + `urllib.error` 足以覆盖 POST + 超时 + 错误
   状态码分流需求，避免 pyproject.toml 依赖膨胀。

6. **指数退避 1/2/4/8/16s 最多 5 次重试**：与 req-03 文案一致。2xx 视为成功不再重试；
   其余状态码或网络异常触发重试。重试在投递线程内同步进行（`_backoff_sleep` 钩子，
   测试可 monkeypatch 为空操作避免真实等待）。

7. **每个订阅独立投递线程**：`deliver_event` 按订阅 `events` 列表匹配活跃订阅，对每个
   匹配订阅起独立 daemon 线程投递。单订阅失败不影响其他订阅；主调用方不阻塞
   （`wait=False` 默认）。`ingest.completed` 在子进程内分发需 `wait=True` 同步等待，
   避免进程退出杀掉 daemon 线程。

8. **投递日志统一一条记录**：每次投递流程（含全部重试）写一条 `WebhookDeliveryLog`，
   `retry_count` 记录最终重试次数，`status_code` / `response_body` / `error_message`
   记录最后一次尝试的结果。避免日志膨胀，便于按 subscription + event_type 排查。

9. **延迟导入避免循环依赖**：`sync_service._emit_sync_completed_event` 与
   `engine._emit_ingest_completed_event` 均在函数体内延迟 `from apps.webhook.deliverer
   import deliver_event`，避免 `apps.webhook` 与 `apps.sync` / `apps.ingest` 间循环
   导入。投递失败不影响主流程（异常被捕获并记日志）。

10. **Webhook 管理端点仅 admin**：Webhook 配置含签名密钥，泄露可伪造事件投递。所有
    CRUD 与投递日志查询端点 `JWTAuth` + `require_admin`，避免普通用户越权查看/修改
    订阅配置。

## 代码实现情况

### POST /datasets/{slug}/sync 处理流程

1. `_require_scope(request, "sync:trigger")` 取 Token。
2. `_get_dataset_or_404(slug, active_only=True)` + 数据集须绑定 `sync_config`（400）。
3. 同步配置存在性 + `is_active` 校验（400）。
4. `check_idempotency(request)`：命中回放 `task_id`，命中 in_progress 返回 409。
5. `get_lock(f"sync:config:{sync_config_id}")`：占用返回 409，释放幂等锁。
6. 启动 daemon 后台线程执行 `SyncService(config).run()`，finally 释放锁与 DB 连接。
7. 写 `SYNC_TRIGGER` 审计，`store_idempotency_result` 存 202 + `task_id` 返回。

### POST /ingest/tasks/{id}/trigger 处理流程

1. `_require_scope(request, "sync:trigger")` 取 Token。
2. `_get_task_or_404(task_id)` 任务存在性校验（404）。
3. `check_idempotency(request)`：命中回放结果。
4. `get_lock(f"ingest:task:{task.pk}")`：占用返回 409（与 `/run` 同锁名）。
5. `spawn_ingest(task.pk)` 子进程执行；失败释放锁与幂等锁，写 FAILURE 审计，返回 500。
6. 成功写 `INGEST_TRIGGER` 审计（含 returncode、rows、duration），存幂等结果返回。

### Webhook 投递流程

1. `deliver_event(event_type, payload, wait=False)` 入口。
2. 查询 `WebhookSubscription.objects.filter(is_active=True)`，按 `events` 列表匹配。
3. 无匹配直接返回；有匹配对每个订阅起 daemon 线程调用 `_deliver_one`。
4. `_deliver_one` 计算 HMAC-SHA256 签名（`X-Webhook-Signature: sha256=<hex>`），
   循环最多 6 次尝试（首次 + 5 次重试），每次失败按 1/2/4/8/16s 退避。
5. finally 写一条 `WebhookDeliveryLog`（含 payload、status_code、retry_count、
   duration_ms、response_body、error_message），关闭本线程 DB 连接。

### 事件分发接入点

- `SyncService.run` 成功路径（`return log` 前）调用 `_emit_sync_completed_event(log)`，
  payload 含 `config_id` / `log_id` / `status` / `mode` / `rows_read` / `rows_written`
  / `rows_skipped` / `duration_ms`。
- `execute_task` 成功路径（`_apply_task_status` 后）调用
  `_emit_ingest_completed_event(task, log)`，仅在 `SUCCESS` / `PARTIAL` 时分发，
  `wait=True` 同步等待（子进程场景）。

## 整合优化情况

- 复用 P8 幂等（`check_idempotency` / `store_idempotency_result` / `release_idempotency`）
  与分布式锁（`get_lock`），与 iter-41 / iter-43 数据集写入端点保持一致的鉴权与并发
  控制模式。
- 复用 iter-41 `ApiTokenAuth` 与 `_require_scope` 模式，`sync:trigger` scope 与
  `datasets:read` / `datasets:write` 并列。
- 复用 `log_audit` 统一审计，新增 `SYNC_TRIGGER` / `INGEST_TRIGGER` / `WEBHOOK_DELIVER`
  三个 `AuditAction` 枚举值，审计日志记录 Token prefix 便于追溯。
- 前端复用既有 `RoleRoute` 守卫、`MainLayout` 菜单结构、`client.ts` axios 实例，
  Webhooks 页面布局与 Datasets / AuditLogs 页面保持一致（列表 + Modal + 抽屉）。

## 测试验证结果

- `uv run ruff check .`：All checks passed。
- `uv run ruff format --check .`：252 files already formatted。
- `uv run pyrefly check`：0 errors（240 suppressed, 1006 warnings not shown）。
- `uv run pytest --cov=backend --cov-report=term-missing`：1598 passed, 36 warnings，
  覆盖率 95.26%（阈值 95%）。
- `manage.py makemigrations webhook --check`：No changes detected（迁移与模型一致）。

测试覆盖：

- **Webhook 模型**：枚举值、`is_subscribed`、名称唯一约束（IntegrityError）、索引、
  `__str__`、`SigningAlgorithm` 默认值。
- **投递器**：HMAC-SHA256 签名头计算、2xx 成功不重试、4xx/5xx 重试、网络异常重试、
  最大重试次数限制、指数退避序列、订阅匹配（events 列表）、`is_active=False` 跳过、
  无匹配订阅不投递、`wait=True` 同步等待、DeliveryLog 记录 payload/timing、
  DeliveryLog 记录最后一次状态码、异常容错（订阅不存在 / HTTP 异常）。
- **Webhook API**：CRUD 权限（admin / regular / 匿名）、名称重复 400、更新时 secret
  为空不更新、投递日志查询过滤 event_type / limit / 404 / 权限。
- **数据集同步触发**：scope 校验（401 / 403）、未绑定 sync_config 400、配置不存在 400、
  配置暂停 400、数据集 inactive 404、slug 不存在 404、幂等回放、分布式锁 409、
  异步执行（线程启动）、审计日志记录。
- **爬取触发**：scope 校验、任务不存在 404、幂等回放、分布式锁 409、`spawn_ingest`
  失败 500 + FAILURE 审计、成功审计含 returncode/rows/duration。
- **审计模型**：`AuditAction` 枚举计数（29→32）与新增值断言。

## 遗留事项

- Webhook 投递为后台线程模型，单进程内并发受限；后续可升级为 Celery/RQ 任务队列
  （req-03 待用户复核项 2）。
- `WebhookDeliveryLog.next_retry_at` 字段已建但本期重试在投递线程内同步进行，未实现
  调度重投（需后台定时任务扫描 `next_retry_at` 重新投递）。
- Webhook secret 明文存储，后续可升级为加密存储（需密钥管理基础设施）。
- 数据集同步触发端点为异步执行，调用方需通过 SyncLog 查询执行结果；未提供执行结果
  查询端点（可复用既有 `GET /sync/logs?config_id=X`）。
- 前端 Webhooks 页面未提供「手动重投」按钮（需新增 `POST /webhooks/{id}/deliveries/
  {log_id}/redeliver` 端点）。
- 前端 TypeScript 检查通过但未引入前端测试框架（Vitest 等），Webhooks 页面无单测。

## 下一轮计划

iter-45：OpenAPI spec 暴露 + 速率限制完善 + P9 测试文档收尾（req-03 item 45）。
