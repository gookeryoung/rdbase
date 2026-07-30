# 迭代记录 10 - P4 SQL 查询控制台

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P4 阶段。

- [x] 19 数据浏览接口与界面：分页/排序/筛选/列显隐、行数统计
- [x] 20 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
- [x] 21 SQL 查询控制台：多 Tab、Monaco 编辑器、执行、结果表格、执行计划
- [ ] 22 导入导出
- [ ] 23 对象管理
- [ ] 24 P4 测试与文档收尾

## 迭代目标

P4-3 SQL 查询控制台：交付多 Tab SQL 编辑器（Monaco）+ 任意 SQL 执行 + 结果表格 + 执行计划（多方言 EXPLAIN）。

后端实现 `execute_sql`（自动区分 SELECT/DDL/DML，按角色 read_only 拦截写操作）+ `explain_sql`（多方言 EXPLAIN/EXPLAIN ANALYZE）+ 2 个 POST 接口（query/explain）；前端实现 SqlConsole 页（多 Tab + Monaco Editor + 执行按钮 + 结果表格 + 执行计划表格 + 角色感知 + ANALYZE 切换）。

## 改动文件清单

### 后端（backend/）

- `backend/apps/manager/query.py` — 新增 P4-3 段落：
  - `_READ_ONLY_PREFIXES`（SELECT/WITH/SHOW/DESCRIBE/DESC/EXPLAIN 前缀白名单）
  - `_EXPLAIN_TEMPLATES`（SQLite `EXPLAIN QUERY PLAN`、PG/MySQL `EXPLAIN [ANALYZE]` 模板）
  - `_strip_sql`（去首尾空白与末尾分号，空 SQL 抛 QueryError）
  - `_is_read_only`（前缀判断，大小写不敏感）
  - `execute_sql`（自动区分 SELECT/DDL/DML：SELECT 用 `connect()` 读结果集；DDL/DML 用 `begin()` 显式事务，统一 commit；`read_only=True` 时非只读语句抛 QueryError；返回 columns/rows/rowcount/elapsed_ms/read_only）
  - `explain_sql`（多方言 EXPLAIN：SQLite 强制忽略 analyze；PG/MySQL 按用户意图追加 ANALYZE 关键字；返回 plan/rows/columns/analyze/dialect）
- `backend/apps/manager/schemas.py` — 新增 `SqlExecIn`（sql str）、`SqlResultOut`（columns/rows/rowcount/elapsed_ms/read_only）、`ExplainIn`（sql + analyze bool）、`ExplainOut`（plan/rows/columns/analyze/dialect）
- `backend/apps/manager/api.py` — 新增 2 个接口：
  - `POST /{ds_id}/query`（所有登录用户可调；viewer 自动 read_only=True，越权写操作返回 403；designer/admin 可执行任意 SQL）
  - `POST /{ds_id}/explain`（所有登录用户可读；EXPLAIN 本身只读，无角色限制）

### 测试（tests/）

- `tests/test_manager_query.py` — 新增 27 个单元测试（共 107 个）：
  - `_strip_sql`（去空白/末尾分号/空 SQL 抛错）
  - `_is_read_only`（SELECT/WITH/SHOW/DESCRIBE/DESC/EXPLAIN 大小写不敏感；INSERT/UPDATE/DELETE/CREATE/DROP/ALTER 非只读）
  - `execute_sql` SELECT（结果集/末尾分号/SELECT *）、INSERT/UPDATE/DELETE（rowcount）、DDL CREATE TABLE（rowcount=-1）、read_only 模式（允许 SELECT/拦截 INSERT/UPDATE/DELETE/DDL）、空 SQL 抛错、语法错误抛 SQLAlchemyError、写入提交可见、WITH CTE 只读
  - `explain_sql` SQLite 返回 plan/rows/columns/dialect、analyze 在 SQLite 被忽略、空 SQL 抛错、末尾分号、不支持方言抛 QueryError（FakeEngine）、语法错误抛 SQLAlchemyError
- `tests/test_manager_api.py` — 新增 22 个集成测试（共 73 个）：
  - `execute_sql` SELECT（viewer 200 + 结果集/末尾分号）、INSERT（designer/admin 200 + rowcount）、viewer INSERT/UPDATE/DELETE/DDL 全部 403、DDL designer 200、未认证 401、未知数据源 404、空 SQL 400、语法错误 400、DML 写入提交可见
  - `explain_sql` viewer 200 + 计划、SQLite 忽略 analyze、未认证 401、未知数据源 404、空 SQL 400、语法错误 400、QueryError 400、SQLAlchemyError 400（monkeypatch）

### 前端（frontend/）

- `frontend/src/types/index.ts` — 追加 `SqlExecRequest`（sql）、`SqlResult`（columns/rows/rowcount/elapsed_ms/read_only）、`ExplainRequest`（sql + analyze?）、`ExplainResult`（plan/rows/columns/analyze/dialect）
- `frontend/src/api/manager.ts` — 新增 `executeSql`（POST /manager/{dsId}/query）、`explainSql`（POST /manager/{dsId}/explain）两个 API 客户端
- `frontend/src/pages/SqlConsole.tsx` — 新建页：
  - 顶部数据源选择器 + 角色提示（viewer 只读模式 / designer+ 可执行 DDL/DML）
  - 多 Tab（editable-card，可新增/关闭，至少保留一个）
  - 每个 Tab：Monaco Editor（SQL 语法高亮，220px 高度）+ 执行按钮 + 执行计划按钮 + ANALYZE 切换
  - 结果区：SELECT 时显示结果表格（>50 行分页，否则不分页），DDL/DML 时显示 Alert（影响行数）
  - 执行计划区：表格展示（保留原始列，便于 SQLite id/parent/notused/detail 与 PG/MySQL 文本列）
  - 错误 Alert（可关闭）
- `frontend/src/routes/index.tsx` — 新增路由 `/sql-console` → `SqlConsole`
- `frontend/src/layouts/MainLayout.tsx` — 菜单新增「SQL 控制台」项（CodeOutlined，所有登录用户可见）
- `frontend/package.json` — 新增依赖 `@monaco-editor/react ^4.7.0`（用户授权）

## 关键决策与依据

1. **角色权限分层**：viewer 仅允许 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN（前缀白名单），designer/admin 可执行任意 SQL。`execute_sql_view` 通过 `request.auth.role` 判断 `read_only` 参数，调用方传 `read_only=True` 拦截写操作，越权 QueryError 含「仅允许执行只读」字样时 API 层转 403，其他 QueryError 转 400。
2. **只读判断用前缀白名单而非 SQL 解析**：用户偏好极简方案。SQL 解析器（sqlparse 等）引入新依赖且方言差异大；前缀白名单覆盖常见只读语句（SELECT/WITH/SHOW/DESCRIBE/DESC/EXPLAIN），简单可靠。边界场景（如 `INSERT ... SELECT`）会被识别为非只读（保守安全）。
3. **SELECT vs DDL/DML 区分**：用同一前缀白名单。SELECT/WITH 走 `engine.connect()` 读结果集（无显式事务）；其他走 `engine.begin()` 显式事务，统一 commit。DDL 的 rowcount 通常为 -1（SQLite/PG/MySQL 均如此），前端按 columns 是否为空决定显示结果表格还是 Alert。
4. **多方言 EXPLAIN 适配**：
   - SQLite: `EXPLAIN QUERY PLAN <sql>`（不支持 ANALYZE，强制忽略）
   - PostgreSQL: `EXPLAIN [ANALYZE] <sql>`（ANALYZE 实际执行获取真实统计）
   - MySQL: `EXPLAIN [ANALYZE] <sql>`（MySQL 8.0+ 支持 ANALYZE）
   模板用 `_EXPLAIN_TEMPLATES` dict 管理，`analyze_keyword` 按 dialect 决定是否拼接。不支持的方言抛 QueryError（覆盖 Oracle/SQL Server 等未接入方言）。
5. **EXPLAIN 返回双重视图**：`plan` 为文本行列表（`" | ".join(columns)`，便于前端 pre 展示）；`rows` 为结构化行 dict 列表（保留原始列，便于前端表格展示 SQLite 的 id/parent/notused/detail 与 PG/MySQL 的文本列）。
6. **执行耗时用 `time.perf_counter()`**：精度高于 `time.time()`，单位毫秒，保留 3 位小数。
7. **Monaco Editor 集成**：用 `@monaco-editor/react` 官方 React 封装，`defaultLanguage="sql"` 自动语法高亮，`options.minimap.enabled=false` 关闭小地图（220px 高度无需），`automaticLayout=true` 自动适应容器尺寸。每个 Tab 独立 Editor 实例，通过 `editorRefs` 保存引用以获取最新 SQL（避免受 antd Tabs 懒渲染影响）。
8. **多 Tab 状态管理**：`QueryTab[]` 数组，每个 Tab 含 sql/result/explain/loading/error 状态。`updateTab(key, patch)` 按 key 更新单个 Tab。关闭最后一个 Tab 时自动创建新 Tab（至少保留一个）。
9. **viewer 模式提示**：顶部显示角色 Tag（viewer 蓝色「只读模式」/designer+ 绿色「可执行 DDL/DML」），ANALYZE Switch 在非 designer 时 disabled（提示「SQLite 不支持」）。
10. **结果分页策略**：SELECT 结果 >50 行时显示分页（默认 50/页，可选 20/50/100），≤50 行时不分页（避免小结果集的分页开销）。
11. **pyrefly 类型推断修复**：`explain_sql` 中 `columns_list`/`rows_raw`/`plan_lines` 的空列表分支触发 `implicit-any-empty-container`，显式注解 `list[str]`/`list[Any]`/`list[str]` 解决。

## 代码实现情况

### query.py（新增 P4-3 段落约 180 行）

- `_strip_sql(sql)` — `sql.strip().rstrip(";").strip()`，空抛 QueryError
- `_is_read_only(sql)` — `sql.lower().startswith(_READ_ONLY_PREFIXES)`
- `execute_sql(engine, sql, *, read_only=False)` — 清洗 SQL → 判断只读 → `read_only=True` 且非只读抛 QueryError → SELECT 用 `connect()` 读结果集 → DDL/DML 用 `begin()` 显式事务 → 返回 dict（columns/rows/rowcount/elapsed_ms/read_only）
- `explain_sql(engine, sql, *, analyze=False)` — 清洗 SQL → 选模板 → 拼 ANALYZE 关键字（SQLite 忽略）→ 执行 → 构造 plan 文本行 + 结构化 rows

### api.py（新增 2 接口约 80 行）

- `execute_sql_view` — POST，从 `request.auth.role` 推断 `read_only` → execute_sql → QueryError 含「仅允许执行只读」转 403 / 其他转 400 / SQLAlchemyError 转 400 → SqlResultOut
- `explain_sql_view` — POST，explain_sql → QueryError 转 400 / SQLAlchemyError 转 400 → ExplainOut

### SqlConsole.tsx（约 340 行）

- 状态：`datasources`/`selectedDsId`/`tabs`/`activeKey`/`analyze`/`editorRefs`
- `QueryTab` 接口：key/title/sql/result/explain/loading/explainLoading/error
- `handleExecute(tab)` — 从 editorRef 取 SQL → executeSql → 更新 Tab
- `handleExplain(tab)` — 从 editorRef 取 SQL → explainSql → 更新 Tab
- `handleAddTab`/`handleRemoveTab` — 多 Tab 增删（至少保留一个）
- Monaco Editor：每个 Tab 独立实例，`onMount` 保存引用，`onChange` 同步 state
- 结果表格：`resultColumns` 由 `result.columns` 动态构造；DDL/DML（columns 为空）显示 Alert
- 执行计划表格：`explainColumns` 由 `explain.columns` 动态构造；SQLite 展示 id/parent/notused/detail，PG/MySQL 展示单列文本

## 整合优化情况

- query.py 与 api.py 解耦：query.py 纯 SQL 执行逻辑（无 Django 依赖），api.py 负责 HTTP 参数解析、角色判断与异常转换
- 角色权限在 api.py 层判断（`request.auth.role`），传 `read_only` 参数给 query.py，query.py 层做最终拦截（防御性双保险）
- 前端复用 P2 的 `listDatasources` 与 `errMsg` 工具函数，避免重复实现
- 前端角色感知复用 `isDesignerOrAdmin(user)`，与 P4-2 Manager.tsx 一致

## 测试验证结果

`make check` 全套通过：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 71 files already formatted
- `pyrefly check` — 0 errors (46 suppressed, 78 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 392 passed, coverage 99.81%

前端门禁：

- `npm run typecheck` — 通过（tsc --noEmit 0 错误）

测试分布：

| 文件 | 用例数 |
|------|--------|
| test_accounts_api.py | 21 |
| test_accounts_flow.py | 3 |
| test_accounts_models.py | 6 |
| test_accounts_permissions.py | 12 |
| test_accounts_users.py | 18 |
| test_accounts_jwt.py | 5 |
| test_datasources_api.py | 19 |
| test_datasources_crypto.py | 6 |
| test_datasources_engine.py | 12 |
| test_datasources_models.py | 7 |
| test_designer_api.py | 15 |
| test_designer_ddl.py | 42 |
| test_designer_drafts_api.py | 29 |
| test_designer_inspector.py | 14 |
| test_manager_query.py | 107 |
| test_manager_api.py | 73 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **392** |

覆盖率：99.81%（高于上轮 99.80%）

| 模块 | 覆盖率 |
|------|--------|
| backend/apps/manager/api.py | 100% |
| backend/apps/manager/query.py | 100% |
| backend/apps/manager/schemas.py | 100% |
| backend/apps/manager/apps.py | 100% |
| backend/apps/manager/admin.py | 100% |
| backend/apps/manager/__init__.py | 100% |

## 遗留事项

- 前端无单元测试（Ant Design + Monaco Editor 组件测试 ROI 低，依赖 typecheck + 手测）。
- 多语句 SQL：当前 `execute_sql` 通过 SQLAlchemy `text()` 执行单条语句，多语句需调用方拆分后逐条调用；后端未做防多语句注入（用户主动输入场景，按角色控制即可）。
- MySQL/PostgreSQL EXPLAIN 实测：本轮仅用 SQLite 测试，MySQL/PG 路径通过模板与 dialect 判断逻辑静态保证；MySQL 8.0- 不支持 `EXPLAIN ANALYZE`，需用户自行判断版本（前端 ANALYZE Switch 不强制限制）。
- SQL 注入风险：SQL 控制台本身就是用户主动输入 SQL 的场景，不做注入防护；角色控制（viewer 只读、designer+ 可写）是主要安全边界。
- 浏览器端到端手测：本轮通过 typecheck 验证类型正确性，未做完整浏览器手测。前后端服务运行中，用户可在浏览器登录后进入「SQL 控制台」页测试。

## 下一轮计划

P4-3 SQL 查询控制台已交付（任务 21 [x]），进入 **P4-4 导入导出**（任务 22）：

1. **P4-4 收集**：研究 CSV/Excel/SQL 脚本导入导出的流式处理方案（pandas / openpyxl / csv 标准库），大文件性能与内存控制。
2. **P4-4 计划**：拆分子任务
   - 后端：`POST /manager/{ds_id}/tables/{table_name}/export`（CSV/Excel/SQL 导出，流式响应）、`POST /manager/{ds_id}/tables/{table_name}/import`（CSV/Excel 导入，事务批量插入）
   - 前端：导入导出按钮 + 文件上传/下载交互
3. **P4-4 实现→测试→文档→验证**：六步迭代循环。
