# iter-19 P6-2 增量冲突与并发

## 需求清单

- [x] SyncConfig 新增主键冲突处理策略字段（upsert/skip/error）与迁移
- [x] schema 与 create/update API 透传并校验 conflict_strategy（非法返回 400）
- [x] 写入层按冲突策略分派：UPSERT 覆盖、SKIP 跳过、ERROR 报错回滚
- [x] SKIP 各方言实现（MySQL INSERT IGNORE、PG/SQLite ON CONFLICT DO NOTHING），以 rowcount 判定是否实际写入
- [x] 批量同步支持线程池并发（max_workers），API 透传
- [x] 补充服务层/模型/API 测试并跑通 make check

## 迭代目标

此前同步写入固定采用 UPSERT（冲突即覆盖），且批量/调度同步只能串行执行。
本轮为同步引入可选的主键冲突处理策略（覆盖/跳过/报错），并让批量同步支持
线程池并发以加速 I/O 密集的多任务同步。

## 改动文件清单

### 新增
- `backend/apps/sync/migrations/0003_syncconfig_conflict_strategy.py` — conflict_strategy 字段迁移

### 修改
- `backend/apps/sync/models.py` — 新增 ConflictStrategy 枚举与 SyncConfig.conflict_strategy 字段
- `backend/apps/sync/sync_service.py` — 写入层按策略分派（_write_single_row/_skip_single_row），run_batch 增加 max_workers 与线程池并发（_run_batch_serial/_run_batch_concurrent/_run_one）
- `backend/apps/sync/schemas.py` — SyncConfigOut/Create/Update 增加 conflict_strategy，SyncBatchIn 增加 max_workers
- `backend/apps/sync/api.py` — create/update 校验 conflict_strategy（_validate_conflict_strategy_or_400），batch-trigger 透传 max_workers
- `tests/test_sync_service.py` — SKIP 各方言 SQL/rowcount、写入分派、三策略端到端、并发调度聚合测试
- `tests/test_sync_models.py` — ConflictStrategy 默认值/取值/持久化测试
- `tests/test_sync_api.py` — conflict_strategy 创建默认/透传/400 校验、max_workers 透传测试

## 关键决策与依据

### 1. 冲突策略作为模型字段而非运行时参数
conflict_strategy 是配置的稳定属性（同一目标表的写入语义应固定），放在 SyncConfig
模型上并随配置持久化，符合「后端模型充实功能」的约定。默认 upsert 保持既有行为向后兼容。

### 2. SKIP 以 rowcount 判定是否写入
INSERT IGNORE / ON CONFLICT DO NOTHING 在冲突时不写入任何行，此时 cursor.rowcount==0，
据此区分「实际写入」与「冲突跳过」并分别计入 written/skipped。部分驱动 rowcount 返回 -1
（未知），按已写入处理避免误报跳过。

### 3. ERROR 策略不吞行级异常，整批回滚
ERROR 策略走纯 INSERT，冲突时数据库自然抛错。在 _write_target_data 中对 ERROR 单独分支，
不套 try/except，使异常穿透到外层 engine.begin() 事务触发整批回滚并抛 SyncError，
语义上「任一行冲突则整批失败」。其它策略仍逐行容错。

### 4. 并发用 ThreadPoolExecutor（线程池）而非进程池
同步任务以数据库 I/O 为主，GIL 期间的 I/O 等待可让线程池并发推进，无需进程池的序列化开销。
每个 worker 结束后调用 connections.close_all() 关闭本线程 Django 连接，避免线程复用时连接泄漏。

### 5. stop_on_error 在并发下为尽力而为
串行模式（_run_batch_serial）遇错精确 break；并发模式（_run_batch_concurrent）收到首个失败后
取消尚未开始的任务（future.cancel()），已在运行的任务仍跑完。两种模式分离实现，语义清晰。

### 6. 并发测试改用 mock 规避 SQLite 单文件锁
测试库为 SQLite 单文件，多线程真实并发写会触发 database table is locked。并发调度测试
（TestSyncServiceBatchConcurrent）与 API 层 max_workers 透传测试改为 mock SyncService.run/run_batch，
只验证调度/聚合/透传逻辑，不触发真实并发 DB 写。生产环境使用 PostgreSQL/MySQL 无此限制。

## 整合优化情况

- 原 _upsert_single_row 中「无 pk 退化为 INSERT」的逻辑上移到 _write_single_row 统一处理，
  三策略共享同一无主键退化路径，_upsert_single_row 仅负责有主键的方言分派。
- SKIP 与 UPSERT 复用相同的列/占位符/参数构造模式，仅冲突子句不同。

## 测试验证结果

- ruff check / format：全绿
- pyrefly：0 errors（Django 描述符误报按既有 pyrefly.toml 降级）
- pytest：全绿，覆盖率 ≥ 上一轮 97.96%
- 新增/改动：sync 模块三策略、SKIP 各方言、并发调度、API 透传/校验均覆盖

## 遗留事项

- 同步监控与告警（成功率/平均耗时统计接口、失败告警记录、前端监控面板）待后续迭代
- 源方言硬编码为 sqlite（_read_source_data）待后续按 source_db_alias 方言化
- 前端尚未暴露 conflict_strategy 选择与 max_workers 设置入口

## 下一轮计划

P6-3 候选：同步监控与告警（统计接口 + 失败告警 + 前端面板），
或源方言化（_read_source_data 去 sqlite 硬编码）。下一轮开始前与用户确认优先级。
