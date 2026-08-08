# iter-38 幂等保护与分布式锁

## 需求清单

- [x] Idempotency-Key 请求头支持（Redis 缓存结果 24h，重复请求返回缓存结果）
- [x] 幂等 key 以「认证主体维度」抽象设计（get_idempotent_subject 返回 user:{pk}，为 P9 API Token 铺路）
- [x] 并发请求 in_progress 标记（重复请求返回 409，TTL 5min 防死锁）
- [x] Redis 分布式锁防同一任务并发执行（SET NX EX，锁超时 30s，获取失败返回 409）
- [x] Lua 脚本/WATCH+MULTI+EXEC 安全释放（校验 value 防误释放）
- [x] 双后端设计：Redis 共享（多 worker）+ 本地内存降级（单 worker）
- [x] strict 模式：Redis 不可用时拒绝加锁（is_distributed 区分 Redis 与本地）
- [x] 锁状态暴露 API（GET /system/locks，admin）
- [x] sync/ingest 触发端点集成幂等 + 锁
- [x] 测试覆盖：双后端/状态机/便捷函数/缓存损坏降级/API/权限/集成

## 迭代目标

P8 健壮性第三项：为 sync/ingest 触发接口提供幂等保证与并发互斥，避免重复触发浪费资源、并发执行导致数据错乱。

## 改动文件清单

### 新增

- backend/apps/system/idempotency.py（IdempotencyManager + _LocalStore/_RedisStore + 便捷函数 check_idempotency/store_idempotency_result/release_idempotency）
- backend/apps/system/distributed_lock.py（DistributedLock + _LocalBackend/_RedisBackend + get_lock/list_lock_info）
- tests/test_system_idempotency.py（35 用例：主体抽象/key 提取/双后端/manager 单例/便捷函数/缓存损坏降级）
- tests/test_system_distributed_lock.py（35 用例：双后端/上下文管理器/strict 模式/配置/工厂/API/权限）

### 修改

- backend/apps/system/schemas.py：新增 LockInfoOut/LockListOut
- backend/apps/system/api.py：新增 GET /system/locks（admin）
- backend/apps/system/redis_client.py：新增 flush_redis()（清理 fakeredis 共享 server 数据，测试用）
- backend/apps/sync/api.py：trigger_sync 端点集成 check_idempotency + get_lock(f"sync:config:{id}")
- backend/apps/ingest/api.py：run_task 端点集成 check_idempotency + get_lock(f"ingest:task:{id}")
- tests/conftest.py：_reset_circuit_breaker fixture 增加 flush_redis（setup + teardown）；_reset_idempotency_and_lock fixture 重置幂等存储与锁后端
- tests/test_sync_api.py：新增 TestTriggerIdempotencyAndLock（3 用例：缓存命中/锁竞争 409/无 key 每次执行）
- tests/test_ingest_api.py：新增 TestRunIdempotencyAndLock（3 用例：缓存命中/锁竞争 409/无 key 每次执行）

## 关键决策与依据

1. **幂等 key 主体抽象**：`get_idempotent_subject(request)` 返回 `user:{pk}`（JWT 场景），P9 API Token 落地后切为 `token:{prefix}`，调用方无需改动。req-03 关键决策第 2 条。
2. **in_progress 标记 TTL 5min**：首请求执行中，重复请求返回 409；TTL 5min 防进程崩溃后死锁（超时后可重试）。已完成结果 TTL 24h（req-03 要求）。
3. **双后端设计**：Redis 共享（多 worker 跨进程）+ 本地内存降级（单 worker，threading.Lock 保护 check-then-act）。Redis 未配置时降级本地内存并记 WARNING。
4. **锁释放用 WATCH/MULTI/EXEC**：原计划用 Lua 脚本（`eval`），但 fakeredis 2.37 不支持 `eval`/`evalsha`（需 lupa 依赖）。改用 WATCH/MULTI/EXEC 实现等价的原子「校验 value → DEL」语义，兼容 fakeredis 与真实 Redis，无需引入新依赖。
5. **strict 模式用 is_distributed()**：`_LocalBackend` 功能可用（is_available=True）但非分布式。引入 `is_distributed()` 方法区分 Redis（True）与本地（False），strict 模式据此拒绝加锁。非 strict 模式仍使用本地内存锁提供进程内互斥（而非无锁放行）。
6. **release 语义**：未持有时检查后端是否被他人持有——无人持有时幂等返回 True，他人持有时返回 False（无权释放）。避免非持有者误释放。
7. **缓存损坏自愈**：`_RedisStore.get()` 检测到损坏 JSON 时删除 key，允许后续 acquire 成功（而非被 SET NX 永久阻塞）。`check_idempotency` 也会在 body 损坏时释放槽位。
8. **fakeredis 共享 server 清理**：`FakeRedis.from_url` 同 URL 跨实例共享 server 数据，`reset_redis_client` 仅清单例不清数据。conftest autouse fixture 增加 `flush_redis()`（setup + teardown），确保跨测试不残留。

## 代码实现情况

- idempotency.py：IdempotencyConfig(frozen) + IdempotencyState(Enum) + IdempotencyRecord + _Store(Protocol) + _LocalStore(threading.Lock) + _RedisStore(SET NX EX + 损坏自愈) + IdempotencyManager(acquire/store_result/release/get/list_keys) + get_manager 单例 + check_idempotency/store_idempotency_result/release_idempotency 便捷函数。
- distributed_lock.py：LockConfig(frozen) + LockInfo + _LockBackend(Protocol, is_distributed) + _LocalBackend(monotonic) + _RedisBackend(SET NX EX + WATCH/MULTI/EXEC 释放) + DistributedLock(acquire/release/__enter__/__exit__/info) + get_lock + list_lock_info + reset_backend。
- 集成模式：sync trigger → check_idempotency（命中缓存直接回放/命中 in_progress 返回 409）→ get_lock（获取失败返回 409 并 release_idempotency）→ 执行业务 → store_idempotency_result（缓存结果）→ release lock。失败路径 release_idempotency 允许重试。

## 整合优化情况

- 移除 distributed_lock.py 中的 `_NoneBackend`（死代码，从未实例化）和 `_NO_LOCK_SENTINEL`（降级标记，被 is_distributed 方案取代）。
- 移除 distributed_lock.py 中的 `_RELEASE_SCRIPT` Lua 脚本常量（改用 WATCH/MULTI/EXEC）。
- test_system_distributed_lock.py 和 test_system_idempotency.py 移除与 conftest 重复的本地 `_reset` fixture（避免 teardown 顺序导致 conftest flush 失效）。

## 测试验证结果

- ruff check：全部通过
- ruff format --check：185 文件已格式化
- pyrefly check：0 errors
- pytest：1312 passed, 8 deselected, 覆盖率 95.56%（≥ 95% 要求）
- 分布式锁模块覆盖率 91%，幂等模块覆盖率 87%（未覆盖分支主要为 Redis 真实连接异常路径与极端竞态，fakeredis 环境无法触发）

## 遗留事项

- Redis 真实环境端到端验证（fakeredis 覆盖功能语义，但未验证真实 Redis 网络异常/重连场景）。
- 幂等缓存无主动清理机制（依赖 TTL 24h 过期，大量幂等 key 可能占用 Redis 内存，P9 可考虑定期清理）。
- 锁的续期机制未实现（长任务超过 30s TTL 会自动释放，可能导致并发；当前 sync/ingest 执行时间远小于 30s，暂不需要）。
- 前端未展示锁状态（GET /system/locks API 已就绪，前端系统状态面板可后续接入）。

## 下一轮计划

进入 iter-39，重点：备份恢复 API + 审计防篡改。POST /system/backup（admin 触发，异步执行）+ GET /system/backups（列表+下载）+ POST /system/restore（二次确认）+ AuditLog 哈希链（prev_hash + hash）+ 哈希校验 API。
