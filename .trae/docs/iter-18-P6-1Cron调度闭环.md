# iter-18 P6-1 Cron 调度闭环

## 需求清单

- [x] 引入 croniter 依赖用于 cron 表达式解析
- [x] 实现 cron 校验与 next_run_at 计算工具模块（scheduling.py）
- [x] SyncConfig 模型内聚 refresh_next_run 方法
- [x] run_scheduled 执行后基于 cron 滚动更新 next_run_at
- [x] create/update/schedule 三个 API 入口计算 next_run_at 并校验非法 cron 返回 400
- [x] run_scheduled_sync 管理命令供系统定时器周期调用

## 迭代目标

补齐同步模块的定时调度闭环：此前 cron_expression 字段存在但无解析逻辑，
next_run_at 从不基于 cron 计算，调度只能靠外部手动触发一次且不会循环。
本轮实现「配置调度 → 计算 next_run_at → 到期执行 → 滚动更新」的自动循环。

## 改动文件清单

### 新增
- `backend/apps/sync/scheduling.py` — cron 校验（is_valid_cron/validate_cron）与 next_run_at 计算（compute_next_run），封装 croniter
- `backend/apps/sync/management/__init__.py`、`commands/__init__.py` — 管理命令包
- `backend/apps/sync/management/commands/run_scheduled_sync.py` — 执行到期定时任务的管理命令
- `tests/test_sync_scheduling.py` — cron 工具单元测试（校验/规范化/next 计算/边界）
- `tests/test_sync_commands.py` — 管理命令测试

### 修改
- `pyproject.toml` — 新增 croniter>=3.0 依赖（用户授权），uv lock 同步
- `backend/apps/sync/models.py` — SyncConfig 新增 refresh_next_run 方法
- `backend/apps/sync/sync_service.py` — run_scheduled 执行后基于 cron 滚动 next_run_at
- `backend/apps/sync/api.py` — create/update/schedule 计算 next_run_at 并校验 cron，新增 _validate_cron_or_400
- `tests/test_sync_models.py` — refresh_next_run 各分支测试
- `tests/test_sync_service.py` — run_scheduled 滚动/失败滚动/非法 cron 分支测试
- `tests/test_sync_api.py` — 创建/更新/调度端点的 cron 计算与 400 校验测试

## 关键决策与依据

### 1. 引入 croniter 而非自实现
cron 时间推算（含 */step、范围、列表、月末、跨月等）边界繁多，自实现易错。
croniter 是成熟库，提供 is_valid 校验与 get_next 计算，经用户授权后引入。

### 2. next_run_at 计算内聚到模型
refresh_next_run 放在 SyncConfig 模型上（而非 UI/API 层），符合「后端 dataclass/模型
充实功能、避免 UI 臃肿」的约定。可调度时基于 cron 计算，不可调度或 cron 非法则清空。

### 3. run_scheduled 滚动不依赖 is_active
单次同步失败会将 status 置为 ERROR（is_active 变 False）。若滚动逻辑依赖 is_active，
失败后 next_run_at 会被清空导致调度中断。因此 run_scheduled 的 finally 分支仅要求
scheduler_enabled + cron 合法即滚动，使定时循环在单次失败后仍能持续，失败只体现在
日志与 status，不打断调度。

### 4. cron 校验在 API 入口而非 schema 层
pydantic field_validator 会让 django-ninja 返回 422，与现有端点的 400 语义不一致。
改为在 create/update/schedule 端点内显式校验（_validate_cron_or_400），非法抛 HttpError(400)，
风格与 require_admin/confirm 校验统一。仅在 scheduler_enabled=True 时校验。

### 5. 调度触发用管理命令而非常驻进程
run_scheduled_sync 管理命令供系统级定时器（Windows 任务计划 / Linux cron / 容器 sidecar）
每分钟调用一次，无需引入常驻调度进程或额外中间件（如 Celery），保持部署简单。

## 整合优化情况

- run_scheduled 原仅更新 last_run_at 且无 cron 计算，重构为滚动更新闭环。
- 复用 scheduling 模块的 is_valid_cron/compute_next_run，模型与 service 均调用同一实现，无重复。

## 测试验证结果

- ruff check / format：全绿
- pyrefly：0 errors（103 suppressed，Django 描述符误报按既有 pyrefly.toml 降级）
- pytest：805 passed，覆盖率 97.96%（≥ 上一轮 97.91%）
- 新增模块覆盖率：scheduling.py 100%、run_scheduled_sync.py 100%、models.py 98%

## 遗留事项

- 增量同步冲突处理策略（跳过/覆盖/报错可选）待后续迭代
- 批量/调度同步并发执行（线程池）待后续迭代
- 同步任务监控面板（成功率/平均耗时/失败告警）待后续迭代
- 源方言硬编码为 sqlite（_read_source_data）待后续按 source_db_alias 方言化

## 下一轮计划

P6-2 候选：同步监控与告警（成功率/平均耗时统计接口、失败告警记录、前端监控面板），
或增量冲突与并发（冲突策略字段、批量同步线程池加速）。下一轮开始前与用户确认优先级。
