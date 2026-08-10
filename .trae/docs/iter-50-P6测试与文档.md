# iter-50：P6 测试与文档

## 需求清单

- [x] 34 P6 测试与文档：sync 增强模块测试补全、用户手册与开发文档更新
  （req-01 P6 item 34，P6 闭环最后一项）

## 迭代目标

补全 sync 模块文档（用户手册 + 开发文档），完成 P6 数据同步增强的全部交付，
使 req-01 P6 item 30-34 全部闭环。

## 改动文件清单

### 修改

- `README.md`：
  - 「开发阶段」新增 P6 条目（定时调度闭环、监控告警、增量冲突与并发、
    源方言化，已完成）。
  - 「运维监控」与「离线内网部署」之间新增「数据同步」章节，含：
    - 概述与核心概念表（SyncConfig/SyncFieldMapping/SyncLog/SyncAlert/
      SyncMode/ConflictStrategy）。
    - API 端点表（16 个端点：配置 CRUD/trigger/preview/schedule、批量触发、
      定时触发、日志、源/目标列、统计、告警列表/确认）。
    - 监控与告警小节：统计接口、告警触发链路、告警确认、前端监控面板。
    - 定时调度小节：croniter 闭环四步、触发方式（管理命令 + API）。
    - 内部架构小节：模型关系图、SyncService 方法树、关键机制（方言化读写、
      冲突策略、熔断重试、分布式锁幂等、事件分发）。
- `.trae/req/req-01-数据库管理平台.md`：item 34 由 `[ ]` 改为 `[x]`。

### 新增

- `.trae/docs/iter-50-P6测试与文档.md`：本迭代记录。

### 删除

- `.trae/docs/iter-45-OpenAPI双视图与令牌桶限流升级.md`：迭代文件数超过 5，
  按「产物约束」保留最新 5 条，清理最旧一条。

## 关键决策与依据

1. **不新建独立文档目录**：项目历史一贯将用户手册与开发文档整合在 README.md
   中（如运维监控、离线部署等章节），保持单一入口便于检索。新增「数据同步」
   章节遵循同一风格。
2. **测试不补**：iter-49 已验收 sync 模块覆盖率 96.01%（≥95% 门禁），187 用例
   覆盖模型/API/服务/命令/调度全链路，无需补全。
3. **文档内容范围**：覆盖 P6 全部交付物（item 30-33 的成果），以「用户手册 +
   开发文档」双视角组织——核心概念与 API 端点面向使用者，内部架构与关键机制
   面向维护者。

## 代码实现情况

iter-50 无新增代码，仅文档同步。

### README.md 新增「数据同步」章节结构

1. **核心概念表**：6 个概念（SyncConfig/SyncFieldMapping/SyncLog/SyncAlert/
   SyncMode/ConflictStrategy）一行说明。
2. **API 端点表**：16 个端点按 配置 CRUD → 触发/预览/调度 → 批量/定时 →
   日志/列信息 → 统计/告警 顺序列出，注明权限要求。
3. **监控与告警**：
   - 统计接口 `aggregate_stats` 算法说明（PARTIAL 不计入成功分子）。
   - 告警触发链路：`SyncService.run` 重试耗尽 → `raise_alert`。
   - 告警确认：`acknowledge` 置位 + 时间戳。
   - 前端监控面板：8 张统计卡片 + 过滤器 + 告警表格 + Badge。
4. **定时调度**：croniter 闭环四步（配置 → 计算 next_run_at → 到期执行 →
   滚动更新）+ 触发方式（管理命令 `run_scheduled_sync` + API `POST /scheduled`）。
5. **内部架构**：
   - 模型关系 ASCII 图（SyncConfig → mappings/logs/alerts）。
   - SyncService 方法树（run/preview/run_batch/run_scheduled + _do_run）。
   - 关键机制 5 项：方言化读写、冲突策略、熔断重试、分布式锁幂等、事件分发。

## 整合优化情况

无。本轮为文档性迭代，无新代码，无重复或冗余引入。

## 测试验证结果

### 测试套件（187 用例全部通过，与 iter-49 一致）

```
uv run pytest tests/test_sync_models.py tests/test_sync_api.py \
  tests/test_sync_service.py tests/test_sync_commands.py \
  tests/test_sync_scheduling.py --cov=apps.sync
→ 187 passed in 12.78s
→ TOTAL 1029 stmts, 37 miss, 148 branch, 8 brpart, 96.01%
```

### Lint

```
uv run ruff check backend/apps/sync/ tests/test_sync_*.py
→ All checks passed!
```

覆盖率与 lint 均与 iter-49 持平，文档变更未引入回归。

## 遗留事项

- req-01 P6（item 30-34）全部交付完毕，P6 里程碑达成（定时调度可自动循环、
  可监控告警）。
- iter-48 遗留（非 req 清单内）：API Token 按数据集细粒度授权、审计哈希链定时
  校验——待用户确认是否推进。
- 项目当前 P0-P6 全部完成，后续若无新需求，可进入收尾或等待新 req。

## 下一轮计划

- req-01 P6 全部闭环。若无新增 req 或 iter-48 遗留事项的明确指示，进入项目
  收尾阶段：输出总结（交付物、关键决策、遗留事项）→ 等待用户确认是否推进
  iter-48 遗留或新需求。
- 若用户确认推进 iter-48 遗留（API Token 数据集细粒度授权 / 审计哈希链定时
  校验），则按六步闭环启动 iter-51。
