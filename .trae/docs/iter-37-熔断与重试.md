# iter-37 熔断与重试

## 需求清单

- [x] 熔断器三态状态机（CLOSED/OPEN/HALF_OPEN）+ 配置（failure_threshold=5/open_seconds=60/half_open_max_calls=3）
- [x] 本地内存后端 + Redis 共享后端（多 worker 跨进程共享，单 worker 降级本地）
- [x] 指数退避重试（max_retries 可配，复用 SyncConfig/IngestTask 现有字段，base_delay/exponential_base/jitter）
- [x] sync 服务接入熔断（SyncService.run 包裹 breaker，重试改为指数退避）
- [x] ingest 服务接入熔断（execute_task 入口检查，OPEN 时不启动 Scrapy）
- [x] 熔断状态暴露 API（GET /system/circuit-states，admin）
- [x] 测试覆盖：状态机/双后端/接入点/API/权限/退避公式

## 迭代目标

P8 健壮性第二项：为外部数据源调用提供熔断保护与指数退避重试，避免下游故障时持续打满请求导致级联雪崩，故障恢复后自动探测放行。

## 改动文件清单

### 新增

- backend/apps/system/circuit_breaker.py（CircuitBreaker + 三态状态机 + _LocalBackend/_RedisBackend + get_breaker 单例 + reset_backend）
- backend/apps/system/retry.py（RetryConfig + compute_backoff + retry_call + with_retry 装饰器）
- tests/test_system_circuit_breaker.py（30 用例：状态机/双后端/单例/snapshot/API/权限）
- tests/test_system_retry.py（13 用例：退避公式/retry_call/装饰器/异常过滤/配置冻结）

### 修改

- backend/apps/system/schemas.py：新增 CircuitStateOut/CircuitStatesOut
- backend/apps/system/api.py：新增 GET /system/circuit-states（admin）
- backend/apps/sync/sync_service.py：run() 包裹熔断器（sync:config:{id}），重试改为指数退避（_backoff_sleep 可注入）
- backend/apps/ingest/engine.py：execute_task() 接入熔断器（ingest:task:{id}），OPEN 时直接记失败日志不启动 Scrapy
- tests/conftest.py：新增 autouse fixture（_reset_circuit_breaker 重置后端+breaker 缓存+redis 单例；_noop_backoff_sleep 替换 sync_service._backoff_sleep 为空操作）
- tests/test_sync_service.py：新增 TestSyncServiceCircuitBreaker（4 用例：失败驱动/成功重置/OPEN 拒绝/退避 sleep）
- tests/test_ingest_engine.py：TestExecuteTask 追加 3 用例（失败驱动/成功重置/OPEN 拒绝）

## 关键决策与依据

1. **三态状态机语义**：CLOSED 放行+失败计数；OPEN 拒绝+计时；HALF_OPEN 限流探测。失败计数为「连续失败次数」，成功即清零（CLOSED/HALF_OPEN 均如此），避免历史失败永久累积。
2. **双后端设计**：本地内存（_LocalBackend，threading.Lock 保护）用于单 worker；Redis 共享（_RedisBackend）用于多 worker 跨进程。Redis 未配置时降级本地内存并记 WARNING，熔断语义仅在本进程生效（不阻断业务）。
3. **时间源分离**：本地后端用 time.monotonic（不受时钟回拨），Redis 后端用 time.time（unix ts，跨进程一致）。breaker 通过 backend.now() 取时间，避免本地与共享后端时钟语义不一致。
4. **多 key 非原子**：Redis 后端各字段独立 key（state/failures/opened_at/half_open_calls），读改写非原子存在轻微竞态。熔断语义容忍（少计一两次失败不破坏整体保护意图），未引入 Lua 脚本（复杂度收益不匹配）。
5. **熔断器 OPEN 不重试**：SyncService.run 在 before_call 抛 CircuitOpenError 时直接包装为 SyncError 抛出，不进入重试循环（下游不可用，重试无意义，避免无谓退避等待）。
6. **退避 sleep 可注入**：sync_service 模块级 _backoff_sleep = time.sleep，测试通过 monkeypatch 替换为空操作，避免重试测试真实等待。conftest autouse fixture 全局替换。
7. **接入点选择**：sync 选 SyncService.run（包裹整个同步执行）；ingest 选 execute_task（子进程入口，OPEN 时不启动 Scrapy 节省资源）。verify_connection 未接入（连接测试本身即探测，加熔断意义有限）。
8. **breaker name 规范**：sync:config:{id}、ingest:task:{id}，便于 circuit-states API 按前缀聚合展示。

## 代码实现情况

- circuit_breaker.py：CircuitBreakerConfig(frozen) + CircuitState(str,Enum) + CircuitOpenError(含 retry_after) + _Backend(Protocol) + _LocalBackend(now=monotonic) + _RedisBackend(now=time.time) + _resolve_backend(单例+锁) + CircuitBreaker(before_call/on_success/on_failure/_maybe_open_to_half_open/reset/snapshot) + get_breaker(单例+锁，忽略二次 config) + list_breakers(sorted) + reset_backend。
- retry.py：RetryConfig(frozen, max_retries/base_delay/max_delay/exponential_base/jitter_ratio) + compute_backoff(min(base*base^attempt, max_delay)+jitter) + retry_call(捕获 retry_on 重试，其它立即抛) + with_retry(装饰器工厂，保留 metadata)。
- sync_service.run：breaker.before_call() → _do_run → on_success/on_failure；失败时 compute_backoff + _backoff_sleep。
- ingest.execute_task：breaker.before_call() OPEN 时记失败日志 return；_run_spider 成功 on_success/失败 on_failure。
- API：circuit_states_view 调 list_breakers().snapshot() 返回列表。
- conftest：_reset_circuit_breaker（autouse，重置 redis_client + circuit_breaker 后端）+ _noop_backoff_sleep（autouse，替换 sync_service._backoff_sleep）。

## 整合优化情况

- 复用 iter-36 的 redis_client.get_redis() 单例，熔断器后端按 Redis 可用性自动选择。
- 复用 iter-36 的 fakeredis 测试基建，Redis 后端测试用 REDIS_FAKE=True。
- sync_service 既有重试循环改造为指数退避，保留 retry_count 累加与最终告警语义，不破坏现有测试断言。
- ingest execute_task 保留原有日志/告警/retry_count 逻辑，仅在入口加熔断检查与 on_success/on_failure 钩子。

## 测试验证结果

- ruff check：All checks passed
- ruff format --check：70 files already formatted
- pyrefly check：0 errors（73 warnings not shown）
- pytest：1242 passed（iter-36 为 1184，+58 用例），覆盖率 97%（iter-36 为 96.23%，↑0.77%）
- 新模块覆盖率：circuit_breaker 97%、retry 100%、api 100%、schemas 100%
- sync_service 96%（含新增熔断逻辑）、ingest/engine 覆盖率不下降

## 遗留事项

- 熔断器状态在 Redis 故障时降级为本地内存，多 worker 下各进程独立熔断（保护范围有限）。生产环境建议配置 REDIS_URL 启用共享。
- Redis 后端多 key 操作非原子，高并发下可能少计失败。如需严格原子可后续引入 Lua 脚本（当前语义容忍）。
- 前端系统状态面板尚未展示熔断器状态（GET /system/circuit-states 已就绪，待前端迭代接入）。
- verify_connection 未接入熔断（连接测试本身即探测），如需可后续按 datasource:{id} 加 breaker。

## 下一轮计划

iter-38：P8 第三项（幂等保护 + 分布式锁）。req-03 第19行：Idempotency-Key 请求头（Redis 缓存 24h，认证主体维度抽象为 P9 铺路）+ Redis 分布式锁（SET NX EX + Lua 释放，锁超时 30s，获取失败 409）+ 锁状态 API。复用 iter-36/37 已落地的 Redis 客户端。
