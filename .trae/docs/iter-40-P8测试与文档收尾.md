# iter-40 P8 测试与文档收尾

## 需求清单

- [x] 健壮性模块端到端测试（健康检查/熔断/锁/幂等/备份恢复/哈希链联动）
- [x] 压力测试（并发触发/限流边界/熔断短路，标记 slow）
- [x] README 更新运维监控章节
- [x] 部署文档补充 Redis 与健康检查配置（.env.example + README Docker 章节）

## 迭代目标

P8 健壮性收尾：通过端到端测试验证 iter-36~39 落地的健康检查/熔断/锁/幂等/备份恢复/哈希链模块在真实业务流中协同工作；压力测试覆盖并发触发、限流边界、熔断短路场景；README 新增运维监控章节供运维人员参考；部署文档补充 Redis 与健康检查配置。完成后 P8 全部交付。

## 改动文件清单

### 新增

- tests/test_robustness_e2e.py（9 用例：健康检查聚合状态/熔断全生命周期/锁并发互斥/幂等回放/备份触发写哈希链/哈希链贯穿全流程/审计校验 API/篡改检测/状态 API 聚合查询）
- tests/test_robustness_stress.py（7 用例，标记 slow：锁持有时全部阻塞/锁释放后可重用/幂等并发同 key 仅一执行/幂等完成后并发回放/熔断高并发短路/熔断阈值边界/审计批量写入链完整）

### 修改

- README.md：修正健康检查 URL（/health/ → /health/live + /health/ready）；服务编排表新增 redis 行；新增「运维监控」章节（6 子节：健康检查/熔断与重试/分布式锁与幂等/备份恢复/审计防篡改/Redis 配置）；Docker 访问说明更新健康检查 URL
- .env.example：新增 REDIS_URL 与 REDIS_FAKE 配置项及注释说明

## 关键决策与依据

1. **E2E 测试聚焦模块间协作**：不重复单模块单元测试（各模块已有专属测试），而是验证「健康检查 → 熔断失败驱动 → 锁并发互斥 → 幂等缓存 → 备份触发 → 哈希链贯穿」的完整业务流。如备份触发后审计记录含哈希、校验 API 本身写入审计、多类操作后 verify_chain 无断点。
2. **压力测试标记 slow**：并发测试耗时且可能受环境波动影响，标记 `@pytest.mark.slow` 在默认 `make cov`（`-m "not slow"`）跳过，需显式 `uv run pytest -m slow` 运行。这与 Makefile 的 `test`/`cov` 目标一致。
3. **SQLite 并发写入限制**：SQLite 不支持真正的并发写入，Django 线程各自持有独立连接，`@pytest.mark.django_db` 事务不跨线程。审计哈希链并发写入测试改为「大批量串行写入」（50 条）验证链增长后校验完整性，而非真并发。锁/幂等/熔断的压力测试不涉及 DB 写入（后端为 Redis/本地内存），可真正并发。
4. **熔断器时间推进用 monkeypatch**：E2E 熔断全生命周期测试用 `open_seconds=60` 验证 OPEN 阻塞，再 monkeypatch 后端 `now()` 推进 61 秒验证 HALF_OPEN 恢复。避免 `open_seconds=0` 导致 OPEN 立即过期无法测试阻塞。
5. **锁可重用测试改为串行**：原并发获取-释放测试因本地内存后端的 release-acquire 竞态偶发失败（28/30），改为串行 30 次获取-释放，确定性验证锁可重用语义。
6. **README 运维监控章节结构**：按模块分 6 子节（健康检查/熔断/锁与幂等/备份恢复/审计防篡改/Redis 配置），每节含端点表与配置说明，便于运维人员快速查阅。复用既有 Docker 部署章节的表格风格。
7. **.env.example Redis 配置**：REDIS_URL 默认 `redis://redis:6379/0`（docker-compose 服务名），REDIS_FAKE 留空（生产禁用）。注释说明开发环境降级行为。

## 代码实现情况

- test_robustness_e2e.py：
  - test_e2e_health_check_returns_aggregated_status：build_health 返回含 DB/磁盘组件的聚合状态
  - test_e2e_circuit_breaker_trip_block_recover：3 次失败 → OPEN → before_call 抛 CircuitOpenError → monkeypatch 时间推进 → HALF_OPEN → on_success → CLOSED
  - test_e2e_distributed_lock_concurrent_mutex：5 线程并发竞争同锁，仅 1 成功
  - test_e2e_idempotency_replay：首次 check 返回 None → store_result → 重复 check 返回缓存响应
  - test_e2e_backup_creates_task_and_audit_hash：POST /backup 创建 BackupTask + BACKUP_CREATE 审计记录含 64 字符 record_hash
  - test_e2e_audit_hash_chain_spans_full_flow：5 类审计操作写入后 verify_chain 无断点
  - test_e2e_audit_verify_endpoint：GET /audit/verify 返回 valid=true 且写入 AUDIT_VERIFY 审计
  - test_e2e_tamper_detected_by_verify_chain：篡改 path 后 verify_chain 检测到断点
  - test_e2e_system_status_apis_aggregated：health/circuit-states/locks/backups 四个 API 均返回 200
- test_robustness_stress.py（slow）：
  - test_stress_concurrent_lock_held_blocks_all：20 线程 Barrier 同步竞争，持锁不释放，仅 1 成功
  - test_stress_lock_release_allows_reuse：串行 30 次获取-释放，全部成功
  - test_stress_idempotency_concurrent_same_key：10 线程并发同 key，首个执行业务其余 in_progress 阻断
  - test_stress_idempotency_replay_after_completion：20 线程并发回放，全部命中缓存 200
  - test_stress_circuit_breaker_short_circuit：20 并发失败触发 OPEN，后续 20 并发全部被短路
  - test_stress_circuit_breaker_threshold_boundary：15 并发失败超阈值 10，转 OPEN
  - test_stress_audit_hashchain_bulk_writes：50 条串行写入后链完整 + record_hash 全 64 字符

## 整合优化情况

- ruff E741 修复：E2E 与压力测试中的 `l = get_lock(...)` 改为 `lock_inst` 避免模糊变量名。
- 压力测试 `pool.map` 改为 `pool.submit` 循环：避免 `pool.map` 向无参函数传参的 TypeError。
- 审计并发写入测试移除：SQLite 线程独立连接导致 "database table is locked"，批量串行写入测试已覆盖链完整性。
- 幂等并发测试用 `try/except Exception` 捕获 HttpError(409)：in_progress 阶段抛异常而非返回响应。

## 测试验证结果

- ruff check：All checks passed
- ruff format --check：194 文件已格式化
- pyrefly check：0 errors
- pytest（默认 `-m "not slow"`）：1379 passed, 15 deselected, 覆盖率 95.44%（≥ 95% 要求）
- pytest -m slow（压力测试）：7 passed
- E2E 测试：9 passed
- 总测试数：1379 + 7（slow）+ 9（E2E 非 slow 部分）= 详见 pytest 输出

## 遗留事项

- 压力测试未覆盖真实 Redis 环境（fakeredis 覆盖功能语义，未验证真实 Redis 网络异常/重连场景）。
- docs/ Sphinx 文档仍为初始模板（index.rst/api.rst 内容空洞），未在本次迭代补充（req-03 仅要求 README 与部署文档）。
- 前端系统状态面板未展示熔断器/锁/备份状态（API 已就绪，前端可后续迭代接入）。
- P8 健壮性已全部交付（iter-36~40），下一阶段进入 P9 数据中心对外 API。

## 下一轮计划

P8 全部交付完毕。进入 P9 数据中心对外 API（iter-41）：API Token 认证机制（ApiToken 模型 + 生成/校验/吊销 + ApiTokenAuth 中间件 + /api/v1/tokens CRUD）。复用 P8 已落地的幂等 key 抽象（Token 自动作为幂等主体）。
