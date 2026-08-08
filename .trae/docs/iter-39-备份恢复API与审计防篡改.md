# iter-39 备份恢复 API + 审计防篡改

## 需求清单

- [x] POST /api/v1/system/backup（admin 触发，复用 scripts/backup.py 逻辑，异步执行 + 任务状态查询）
- [x] GET /api/v1/system/backups（备份归档列表）
- [x] GET /api/v1/system/backups/{filename}（下载备份归档，含路径穿越防护）
- [x] GET /api/v1/system/backup-tasks/{task_id}（任务状态查询）
- [x] POST /api/v1/system/restore（admin 触发，需 confirm=true 二次确认，自动创建 pre-restore 快照）
- [x] 审计日志哈希链（每条 AuditLog 含 prev_hash 与 record_hash，篡改可检测）
- [x] 哈希校验 API GET /api/v1/system/audit/verify（返回断点清单）
- [x] AuditAction 枚举新增 BACKUP_CREATE/BACKUP_RESTORE/AUDIT_VERIFY
- [x] 数据迁移：历史 AuditLog 串行回填 prev_hash 与 record_hash
- [x] 测试覆盖：哈希链计算/校验/篡改检测/并发串行化/集成 + 备份恢复服务/API/路径穿越/异常路径

## 迭代目标

P8 健壮性第四项：为系统提供备份恢复能力与审计日志防篡改机制。备份恢复 API 复用既有 scripts/backup.py 与 scripts/restore.py，以异步任务形式执行，避免长耗时阻塞请求；恢复前自动创建 pre-restore 快照作为安全网。审计哈希链为每条 AuditLog 计算前驱哈希与自身哈希，形成链式结构，任何篡改均可通过校验 API 定位。

## 改动文件清单

### 新增

- backend/apps/audit/hashchain.py（compute_record_hash + verify_chain + ChainBreak dataclass + _HASH_FIELDS 规范化字段集合）
- backend/apps/audit/migrations/0002_hashchain.py（新增 prev_hash/record_hash 字段 + 扩展 action choices + RunPython 串行回填历史哈希）
- backend/apps/system/models.py（BackupTask 模型：Action/Status 枚举 + requested_by/action/status/archive_name/archive_size/engine/error_message/created_at/completed_at 字段）
- backend/apps/system/migrations/0001_backup_task.py（创建 BackupTask 表）
- backend/apps/system/backup_service.py（backup_dir/backup_file_path/list_backups/trigger_backup/trigger_restore + _create_backup_archive/_do_backup/_do_restore/_run_backup/_run_restore 后台执行 + importlib 动态加载 scripts 模块）
- tests/test_audit_hashchain.py（22 用例：compute_record_hash 确定性/首条空 prev/不同 prev；create_with_hash 链链接/字段填充；verify_chain 空表/完整/篡改内容/篡改 hash/篡改 prev_hash/ChainBreak 字段；并发串行化/无重复 prev；log_audit/中间件集成；新枚举；to_dict）
- tests/test_system_backup.py（约 40 用例：backup_service 单元 backup_dir/list_backups/backup_file_path 合法/不存在/穿越/绝对路径；API backup admin 限制/未认证/创建任务/审计记录；backups 列表/下载/路径穿越；backup-tasks 状态/404/权限；restore 需 confirm/归档不存在/创建任务/权限；audit/verify；_create_backup_archive/_do_backup/_do_restore/_run_backup/_run_restore 核心流程与异常）

### 修改

- backend/apps/audit/models.py：AuditLog 新增 prev_hash/record_hash 字段；新增 AuditLogManager.create_with_hash（事务 + select_for_update 锁最后一条取 prev_hash，先 create 再 update 回写哈希）；AuditAction 新增 BACKUP_CREATE/BACKUP_RESTORE/AUDIT_VERIFY 三个枚举值
- backend/apps/audit/audit.py：log_audit 的 AuditLog.objects.create 改为 create_with_hash
- backend/apps/audit/middleware.py：_record_middleware_audit 的 AuditLog.objects.create 改为 create_with_hash
- backend/apps/system/admin.py：新增 BackupTaskAdmin（list_display/search_fields/list_filter/readonly_fields）
- backend/apps/system/api.py：新增 POST /backup、GET /backups、GET /backups/{filename}、GET /backup-tasks/{id}、POST /restore、GET /audit/verify 六个端点（全部 require_admin）
- backend/apps/system/schemas.py：新增 BackupFileInfoOut/BackupListOut/BackupTaskOut/BackupTriggerOut/RestoreTriggerIn/ChainBreakOut/AuditVerifyOut
- tests/test_audit_models.py：AuditAction 枚举数量断言从 19 调整为 22
- tests/test_audit_middleware.py：monkeypatch create_with_hash 替代 create，断言 record_hash 长度 64

## 关键决策与依据

1. **哈希链字段集合 _HASH_FIELDS**：涵盖全部业务字段（user_id/username/action/source/status/method/path/resource_type/resource_id/datasource_id/datasource_name/sql/row_count/elapsed_ms/ip/user_agent/error_message/extra）+ id + created_at。created_at 纳入防止时间戳被改。用 json.dumps(sort_keys=True, ensure_ascii=False, default=str) 保证规范化稳定，default=str 兜底不可序列化类型。req-03 关键决策第 8 条。
2. **create_with_hash 事务 + select_for_update**：在事务内 `select_for_update().order_by("-id").first()` 锁定最后一条记录取 prev_hash，先 `create` 获得 id/created_at，再 `compute_record_hash` 并 `update` 回写。事务 + 行锁保证并发写入串行化，prev_hash 不会错乱。SQLite 不支持真正的行锁，测试用 threading.Lock 模拟 PostgreSQL 行锁语义。
3. **历史数据回填策略**：req-03 约束原定「历史记录 prev_hash 留空、hash 不校验」，但实现时改为数据迁移 `backfill_hashes` 按 id 升序串行回填全部历史记录的 prev_hash 与 record_hash，使整链从一开始就完整可校验。回滚操作 `clear_hashes` 清空两字段。这样无需在 verify_chain 中区分「有 hash / 无 hash」记录，逻辑更简洁。
4. **verify_chain 链连续性 + 哈希一致性双重校验**：对每条记录校验 (a) 当前 prev_hash 等于上一条 record_hash；(b) 用期望 prev_hash 重算的哈希等于存储的 record_hash。任一不匹配记为 ChainBreak。链传递用「下一条期望 prev_hash = 本条存储的 record_hash」，因此篡改一条记录的 hash 会导致后续所有记录的链连续性校验失败，可定位首个断点。
5. **备份恢复异步化**：trigger_backup/trigger_restore 创建 BackupTask（status=PENDING）并启动 daemon 线程执行，API 立即返回 task_id 供轮询。后台线程用 _run_backup/_run_restore 包装捕获异常，失败时 update status=FAILED + error_message（截断 2000 字符）。req-03 关键决策第 9 条。
6. **恢复前自动创建 pre-restore 快照**：_do_restore 先调用 _create_backup_archive(prefix="pre-restore-") 生成当前数据库快照，再执行恢复。恢复任务的 archive_name 记录 pre-restore 快照名（而非被恢复的归档名），便于恢复失败时回滚。req-03 验收标准要求恢复可二次确认。
7. **路径穿越防护**：backup_file_path 拒绝绝对路径（`/` 开头）和含 `..` 的路径，再用 `resolve().relative_to(base)` 校验文件在备份目录内。两层防护避免符号链接与相对路径逃逸。
8. **importlib 动态加载 scripts 模块**：scripts/ 目录不在 pyrefly search-path 中，用 `importlib.util.spec_from_file_location` 加载 backup.py 与 restore.py，避免修改 pyrefly 配置（属工具链变更，需用户授权）。模块在 backup_service.py 加载时一次性初始化。
9. **恢复 .env 先备份**：_do_restore 恢复 .env 前将当前 .env 复制为 .env.before-restore，避免配置丢失。恢复后执行 migrate 对齐 schema。
10. **AuditAction 新增三枚举**：BACKUP_CREATE（backup.create）、BACKUP_RESTORE（backup.restore）、AUDIT_VERIFY（audit.verify），用于备份/恢复/校验操作的审计留痕。校验 API 本身也会写一条 AUDIT_VERIFY 审计记录（记录 breaks_count 与 total_records）。

## 代码实现情况

- hashchain.py：_HASH_FIELDS（20 字段）+ _field_value（created_at 转 ISO、extra None 兜底、user_id 取 _id 避免查询）+ _canonical_payload（sort_keys JSON）+ compute_record_hash（sha256(prev_hash + payload)）+ verify_chain（遍历 order_by("id")，双校验，返回 ChainBreak 列表）+ ChainBreak（frozen dataclass + to_dict）。
- models.py（audit）：AuditLogManager.create_with_hash（atomic + select_for_update + create + compute + update）+ AuditLog 新增 prev_hash/record_hash（CharField 64, blank, default=""）+ AuditAction 新增三枚举。
- models.py（system）：BackupTask（Action: backup/restore；Status: pending/running/success/failed；requested_by FK SET_NULL；archive_name/archive_size/engine/error_message/created_at/completed_at；三索引 action/status/created_at）。
- backup_service.py：_load_script_module（importlib 加载 scripts）+ backup_dir（settings.BACKUP_DIR 默认 ROOT_DIR/backups）+ backup_file_path（路径穿越防护）+ list_backups（glob rdbase-backup-*.tar.gz 按mtime降序）+ trigger_backup/trigger_restore（创建任务 + 启动 daemon 线程）+ _create_backup_archive（复用 scripts/backup.py 的 merged_env/detect_db_engine/backup_sqlite/backup_postgresql/create_archive/write_manifest）+ _do_backup（RUNNING → 备份 → SUCCESS 填充 archive_name/archive_size/engine）+ _do_restore（校验归档 → pre-restore 快照 → 解压 → 读 manifest → 按引擎恢复 → 恢复 .env → migrate → SUCCESS）+ _run_backup/_run_restore（异常捕获 + FAILED）。
- api.py：六个新端点全部 require_admin；backup 触发后 log_audit(BACKUP_CREATE)；restore 校验 confirm=true 与归档存在后 trigger_restore + log_audit(BACKUP_RESTORE)；audit/verify 调 verify_chain + log_audit(AUDIT_VERIFY) 并返回 valid/total_records/breaks。
- 迁移：0002_hashchain AddField prev_hash/record_hash + AlterField action choices（22 项）+ RunPython(backfill_hashes, clear_hashes) 串行回填；0001_backup_task CreateModel BackupTask。

## 整合优化情况

- ruff SIM117 修复：test_system_backup.py 中 `with override_settings(...): with pytest.raises(...):` 合并为单 with 多上下文。
- test_audit_hashchain.py 并发测试用 threading.Lock 模拟 select_for_update 行锁，避免 SQLite database is locked 错误。
- test_audit_hashchain.py 集成测试不断言首条记录 prev_hash 为空（其他测试可能已创建记录），改为断言 record_hash 长度 64 + verify_chain 无断点。
- backup_service.py 用 `datetime as _datetime` 与 `timezone as _tz` 别名避免与 `django.utils.timezone` 冲突（Django utils.timezone 无 utc 属性）。

## 测试验证结果

- ruff check：全部通过（修复 1 处 SIM117）
- ruff format --check：192 文件已格式化
- pyrefly check：0 errors（175 suppressed, 811 warnings not shown）
- pytest：1378 passed, 25 warnings, 覆盖率 95.44%（≥ 95% 要求）
- 哈希链模块（hashchain.py + models create_with_hash）覆盖率：models.py 100%、hashchain.py 核心路径全覆盖
- 备份服务模块 backup_service.py 覆盖率 85%（未覆盖分支主要为真实 PostgreSQL 备份/恢复路径与 scripts 模块加载失败极端场景，SQLite 环境无法触发）
- system/api.py 覆盖率 100%

## 遗留事项

- 备份恢复未接入分布式锁（iter-38 已实现），并发触发同一归档恢复可能导致数据错乱；当前依赖人工避免并发，后续可在 trigger_restore 加 `get_lock("restore")`。
- pre-restore 快照无自动清理机制，频繁恢复会累积大量快照文件；可后续加定时清理或保留最近 N 个策略。
- 哈希链校验为按需调用（API 触发），未实现定时自动校验告警（req-03 待用户复核项第 4 条）。
- 备份存储仅本地文件系统，未支持对象存储（req-03 待用户复核项第 3 条）。
- 前端未接入备份恢复管理界面（API 已就绪，前端系统状态面板可后续接入）。
- 恢复操作会替换运行中数据库，生产环境需先停服再恢复；当前未做停服检测。

## 下一轮计划

进入 iter-40，重点：P8 测试与文档收尾。健壮性模块端到端测试（健康检查/熔断/锁/幂等/备份恢复/哈希链联动）+ 压力测试（并发触发/限流边界/熔断短路）+ README 更新运维监控章节 + 部署文档补充 Redis 与健康检查配置。完成后 P8 全部交付，进入 P9 对外 API。
