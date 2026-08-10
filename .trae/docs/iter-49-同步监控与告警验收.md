# iter-49：同步监控与告警验收

## 需求清单

- [x] 31 同步监控与告警：成功率/平均耗时统计接口、失败告警记录、前端监控面板
  （req-01 P6 item 31，前序迭代已实现代码，本轮验收门禁与勾选）

## 迭代目标

确认 req-01 P6 item 31「同步监控与告警」三项交付物完整、门禁通过、覆盖率
不下降，并勾选需求清单。

## 改动文件清单

### 修改

- `.trae/req/req-01-数据库管理平台.md`：item 31 由 `[ ]` 改为 `[x]`。

### 新增

- `.trae/docs/iter-49-同步监控与告警验收.md`：本迭代记录。

### 删除

- `.trae/docs/iter-44-调度触发API与Webhook订阅.md`：迭代文件数超过 5，
  按「产物约束」保留最新 5 条，清理最旧一条。

## 关键决策与依据

1. **不补代码**：req-01 P6 item 31 三项要求均已在前序迭代交付并通过测试，
   本轮仅做验收与勾选，避免无意义改动。
2. **保留既有架构**：监控告警链路为「`SyncService.run` 重试耗尽 →
   `SyncAlert.raise_alert` → `GET /sync/alerts`/`POST /ack` → 前端面板」，
   统计走 `SyncLog.aggregate_stats` + `GET /sync/stats`，前端 Sync.tsx
   监控面板含 8 张统计卡片 + 告警表格 + 过滤器，结构清晰，无需重构。

## 代码实现情况

iter-49 无新增代码，验收既有实现：

### 后端

- `backend/apps/sync/models.py`
  - `SyncStats`（dataclass）：total/succeeded/partial/failed/success_rate/
    avg_duration_ms/total_rows_read/total_rows_written/total_rows_skipped。
  - `SyncLog.aggregate_stats(config_id, days)`：按配置 ID 与最近 N 天聚合，
    success_rate 保留一位小数，PARTIAL 不计入成功分子。
  - `SyncAlert` 模型：config FK + level + message + acknowledged +
    acknowledged_at + created_at；`raise_alert(config, message, level)` 类方法、
    `acknowledge(save)` 实例方法。
  - `AlertLevel` 枚举：WARNING/ERROR。
- `backend/apps/sync/api.py`
  - `GET /sync/stats?config_id=&days=`：返回 SyncStatsOut。
  - `GET /sync/alerts?config_id=&acknowledged=&level=&limit=`：返回
    SyncAlertListOut（含 items + total + unacknowledged），level 非法抛 400。
  - `POST /sync/alerts/{alert_id}/ack`：标记已确认并返回 SyncAlertOut，不存在
    抛 404。
- `backend/apps/sync/sync_service.py` L161：`SyncService.run` 重试耗尽时调
  `SyncAlert.raise_alert(config, str(exc), level=AlertLevel.ERROR)`，仅最终
  失败告警一次，避免每次重试都产生告警。

### 前端

- `frontend/src/api/sync.ts`：`getSyncStats`、`listSyncAlerts`、`ackSyncAlert`。
- `frontend/src/pages/Sync.tsx`：
  - 顶部「监控面板」按钮配 Badge 显示未确认告警数（`loadUnackCount`）。
  - 监控面板对话框：统计范围 Segmented（近 7 天/近 30 天/全部）+ 8 张统计卡片
    （成功率/执行次数/失败次数/平均耗时/成功/部分成功/累计写入/累计跳过）+
    失败告警表格（级别/配置/内容/时间/状态/确认操作）+「仅看未确认」过滤。
  - `handleAckAlert` 调 `ackSyncAlert` 后刷新面板。

## 整合优化情况

无。本轮为验收性迭代，无新代码，无重复或冗余引入。

## 测试验证结果

### 测试套件（187 用例全部通过）

```
uv run pytest tests/test_sync_models.py tests/test_sync_api.py \
  tests/test_sync_service.py tests/test_sync_commands.py \
  tests/test_sync_scheduling.py
→ 187 passed in 12.35s
```

关键覆盖点：
- `test_sync_models.py`：
  - `SyncLog.aggregate_stats` 多场景（空、混合状态、按 config_id、按 days=7、
    days=0 不限）。
  - `SyncAlert.raise_alert` 默认 error 级别、warning 级别。
  - `SyncAlert.acknowledge` 置位 + 时间戳、`save=False` 不落库。
  - 级联删除、按 created_at 倒序、__str__。
- `test_sync_api.py`：
  - `TestSyncStatsAPI`：空统计、按 config_id 过滤、非管理员 403。
  - `TestSyncAlertAPI`：列表 + unacknowledged 计数、acknowledged 过滤、
    config_id 过滤、level 非法 400、ack 端点、ack 不存在 404、非管理员 403。
- `test_sync_service.py`：
  - `run` 重试耗尽产生一条 SyncAlert；成功路径不产生告警。

### Lint 与类型检查

```
uv run ruff check backend/apps/sync/ tests/test_sync_*.py
→ All checks passed!

uv run pyrefly check backend/apps/sync/
→ INFO 0 errors (19 suppressed, 136 warnings not shown)
```

### 覆盖率

```
uv run pytest tests/test_sync_*.py --cov=apps.sync --cov-report=term-missing
→ TOTAL 1029 stmts, 37 miss, 148 branch, 8 brpart, 96.01%
→ Required test coverage of 95.0% reached.
```

各文件：
- `api.py` 93%（未覆盖：limit 边界、source/target-columns 异常分支）
- `models.py` 99%（未覆盖：SyncFieldMapping.__str__、SyncLog.__str__）
- `sync_service.py` 95%（未覆盖：批量串行 stop_on_error 路径、并发取消分支、
  各方言 UPSERT 内部行级异常、`_emit_sync_completed_event` 异常吞掉分支）
- 其余文件 100%

未覆盖行均为异常分支或 __str__，整体 96.01% 与 iter-46 持平，未下降。

## 遗留事项

- req-01 P6 item 34「P6 测试与文档：sync 增强模块测试补全、用户手册与开发文档
  更新」仍未完成。本轮已确认 sync 模块测试覆盖完整（96.01%），剩余主要是
  用户手册与开发文档的同步更新，留待后续迭代。
- iter-48 遗留（非 req 清单内）：API Token 按数据集细粒度授权、审计哈希链定时
  校验——待用户确认是否推进。

## 下一轮计划

- 启动 iter-50：req-01 P6 item 34「P6 测试与文档」。
  - 收集：盘点 sync 模块文档现状（用户手册/开发文档/README 中 sync 章节）。
  - 计划：补全用户手册「同步监控与告警」一节（如何查看统计、如何确认告警）、
    开发文档「监控告警架构」一节（SyncAlert 触发链路、aggregate_stats 算法）。
  - 实现 → 测试（文档校验脚本若有）→ 文档 → 验证。
- 目标：req-01 P6 全部交付（item 30-33 已完成，item 34 完成后 P6 闭环）。
