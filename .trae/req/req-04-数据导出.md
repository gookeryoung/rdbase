# 需求：数据导出增强与统一（P9）

## 概述

在现有表导出（P4-4 `POST /manager/{ds_id}/tables/{table_name}/export`）与 SQL
结果集导出（`POST /manager/{ds_id}/query/export`）基础上，补全数据集行导出场景，
统一三类导出对象的权限过滤、审计日志与限流配额，并在前端数据集详情页加入导出入口。

复用现有流式导出基础设施（`StreamingHttpResponse` + `rows_to_csv` /
`iter_table_rows` / `execute_sql`），不重复造轮子。导出格式以 CSV 为主（用户确认），
保留现有 xlsx/sql/json 格式不删除。

## 现状基线

### 已有（P4-4 交付）

- `POST /manager/{ds_id}/tables/{table_name}/export`：表导出，支持 csv/sql/xlsx，
  CSV/SQL 流式（`StreamingHttpResponse`），xlsx 一次性 `write_only` 模式。
- `POST /manager/{ds_id}/query/export`：SQL 结果集导出，支持 csv/json/xlsx，
  强制 `read_only=True` 拦截 DDL/DML。
- `GET /audit/logs/export`：审计日志 CSV 导出，仅管理员。
- 前端 `SqlConsole.tsx` / `Manager.tsx` 已有导出按钮与下载逻辑。
- `backend/apps/manager/query.py`：`iter_table_rows` / `rows_to_csv` /
  `rows_to_sql` / `export_excel` / `export_sql_result_excel` 流式工具函数。

### 缺失（P9 补全）

- 数据集行导出端点：`datasources` 模块无导出端点，数据集（`Dataset`）的行只能通过
  `GET /api/v1/datasets/{slug}/rows` 分页查询，无法一键导出全量 CSV。
- 导出审计：现有三个导出端点均 `del request`，不写 `AuditLog`，无法追溯谁导出了
  什么数据。
- 权限过滤：表导出 / SQL 导出未应用行级权限与列级白名单（P4 已实现的
  `RowFilter` / `ColumnVisibility` 在导出路径被绕过）。
- 导出限流：无配额限制，可无限次导出整张大表，存在数据泄露与性能风险。
- 前端数据集页无导出入口。

## 定位

P9 是 P4 数据管理的纵深增强，与 P8（清洗）平级：

- P9-Q1 数据集行导出：新增 `GET /api/v1/datasets/{slug}/export` 端点，复用
  `DatasetRow` 查询逻辑 + `rows_to_csv` 流式输出。
- P9-Q2 导出权限与审计：三个现有端点 + 新端点统一加 `AuditAction.EXPORT` 审计日志，
  表导出与数据集导出应用行级权限/列级白名单过滤。
- P9-Q3 导出限流与配额：按用户维度令牌桶限流 + 每日导出行数配额（可配置）。
- P9-Q4 前端导出统一：数据集详情页加导出按钮，导出格式下拉统一为 CSV（默认）。
- P9-Q5 测试与文档：覆盖率 ≥ 95% 回归 + external-api-guide 同步 + 端到端用例。

## 需求清单

### P9-Q1 数据集行导出（里程碑：数据集详情页可一键导出 CSV）

- [x] 50 新增 `GET /api/v1/datasets/{slug}/export` 端点：
  - Query 参数：`format=csv`（仅 CSV，用户确认）、`columns=`（可选字段裁剪，逗号分隔）、
    `filter=`（可选筛选条件，复用 `GET /datasets/{slug}/rows` 的筛选语法）。
  - 响应：`StreamingHttpResponse`，`text/csv; charset=utf-8`，含 UTF-8 BOM，
    `Content-Disposition: attachment; filename="{slug}.csv"`。
  - 复用 `DatasetRow` 查询逻辑（`apps.datasources.dataset.list_rows`），游标分块
    读取避免 OOM，每块 1000 行。
  - 权限：所有登录用户可读（与 `GET /datasets/{slug}/rows` 一致），但应用行级权限
    与列级白名单（见 Q2）。
- [x] 51 数据集行导出限流：按 `dataset_export:{user_id}` 维度令牌桶限流，容量
  `RATE_LIMIT_DATASET_EXPORT_CAPACITY`（默认 10）、每秒补充
  `RATE_LIMIT_DATASET_EXPORT_REFILL_RATE`（默认 0.5，即每 2 秒 1 次）。
- [x] 52 前端数据集详情页加「导出 CSV」按钮：点击触发浏览器下载，下载中显示
  loading 状态，完成后 toast 提示。

### P9-Q2 导出权限与审计（里程碑：导出操作可追溯、权限过滤生效）

- [ ] 53 `AuditAction` 新增 `EXPORT` 枚举值（choices 总数 +1）。
- [ ] 54 三个现有端点 + 新端点统一写 `AuditLog`：
  - `action=AuditAction.EXPORT`，`resource_type` 为 `dataset` / `table` / `sql` /
    `audit_log`（对应四类导出对象）。
  - `resource_id` 为数据集 slug / `{ds_id}:{table_name}` / `{ds_id}:sql` /
    `audit`。
  - `extra` 含 `format` / `rows_exported`（流式完成后写入）/ `duration_ms` /
    `columns`（字段裁剪情况）。
  - 流式响应：审计日志在生成器消费完成后写入（`finally` 块），失败也记录
    `status=failure` + `error_message`。
- [ ] 55 表导出 `export_table_view` 应用行级权限与列级白名单：
  - 复用 `apps.manager.security` 的 `RowFilter` / `ColumnVisibility` 逻辑
    （P4 已实现，导出路径当前绕过）。
  - 列级白名单：仅导出用户可见列，不可见列不出现在 CSV 表头与行数据中。
  - 行级权限：按用户角色与数据集行权限配置过滤行（如 viewer 仅导出
    `status='active'` 的行）。
- [ ] 56 数据集行导出同样应用行级权限与列级白名单（与 `GET /rows` 一致）。
- [ ] 57 SQL 结果集导出 **不应用** 行级权限/列级白名单（SQL 为用户自定义查询，
  权限由 SQL 本身控制；仅记录审计日志）。

### P9-Q3 导出限流与配额（里程碑：防滥用导出）

- [ ] 58 令牌桶限流：所有导出端点按 `export:{user_id}` 维度限流，容量
  `RATE_LIMIT_EXPORT_CAPACITY`（默认 20）、每秒补充
  `RATE_LIMIT_EXPORT_REFILL_RATE`（默认 1.0）。超限返回 429 + `Retry-After` 头。
  - 复用现有 `apps.system.rate_limit` 令牌桶实现（与 webhook 限流同模式）。
- [ ] 59 每日导出行数配额：按 `export_quota:{user_id}:{date}` 维度计数，上限
  `EXPORT_DAILY_ROW_QUOTA`（默认 1,000,000 行/天）。超限返回 429 +
  `Retry-After: <次日 00:00>`。
  - 流式响应中实时累加行数，超限时生成器提前终止并追加 `[配额超限，导出已截断]`
    提示到响应体尾部。
- [ ] 60 管理员可在系统设置页查看与调整配额（`SystemSetting` 新增
  `export_daily_row_quota` 字段，默认 1,000,000）。

### P9-Q4 前端导出统一（里程碑：三类导出入口体验一致）

- [ ] 61 数据集详情页「导出 CSV」按钮（Q1 item 52 已含，此处统一下拉格式）。
- [ ] 62 表管理页「导出」下拉统一为 CSV（默认）/ Excel / SQL 三项（现有已支持，
  此处仅确认不改动）。
- [ ] 63 SQL 控制台「导出」下拉统一为 CSV（默认）/ JSON / Excel 三项（现有已支持，
  此处仅确认不改动）。
- [ ] 64 导出限流 429 响应前端统一处理：toast 提示「导出过于频繁，请 X 秒后重试」，
  X 来自 `Retry-After` 响应头。

### P9-Q5 测试与文档（里程碑：覆盖率回归 + 手册同步）

- [ ] 65 数据集行导出端点测试：全量导出 / 字段裁剪 / 筛选 / 空数据集 / 权限过滤 /
  限流 429 / 配额超限截断。
- [ ] 66 现有表导出与 SQL 导出补权限过滤测试（行级权限生效、列级白名单生效）。
- [ ] 67 导出审计日志测试：四类导出均写 AuditLog，流式失败也记录。
- [ ] 68 `external-api-guide.rst` 新增「数据导出」章节：四类导出端点用法、
  权限、限流、配额、审计说明。
- [ ] 69 `changelog.rst` 追加 v0.1.0 P9-Q1~Q5 已交付条目。
- [ ] 70 覆盖率 ≥ 95% 回归（不低于 P8 基线 95.43%）。

## 约束

- **不引入新依赖**：复用现有 `csv` / `openpyxl` / `StreamingHttpResponse`。
- **不改变现有端点 URL 与响应格式**：Q2 权限过滤与审计为增强，不破坏向后兼容。
- **导出格式以 CSV 为主**：用户确认仅需 CSV，但现有 xlsx/sql/json 保留不删除。
- **流式同步优先**：用户确认大数据量策略为流式同步+分块，不引入异步任务队列
  （Celery 等）。
- **权限过滤复用 P4 实现**：不重复实现 `RowFilter` / `ColumnVisibility`，直接
  复用 `apps.manager.security` 现有逻辑。
- **限流复用现有令牌桶**：不重复实现，直接复用 `apps.system.rate_limit`。

## 验收标准

1. `GET /api/v1/datasets/{slug}/export` 端点可用，流式返回 CSV，含 BOM。
2. 四类导出端点均写 `AuditAction.EXPORT` 审计日志（成功 / 失败）。
3. 表导出与数据集导出应用行级权限与列级白名单（viewer 角色导出受限数据验证通过）。
4. 导出限流：超容量返回 429 + `Retry-After`；超日配额截断并提示。
5. 前端数据集详情页有「导出 CSV」按钮，点击下载文件。
6. `make check` 全套门禁通过，覆盖率 ≥ 95.43%。
7. `external-api-guide.rst` 与 `changelog.rst` 同步更新。
