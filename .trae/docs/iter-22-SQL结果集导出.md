# iter-22 SQL 结果集导出

## 需求清单

- [x] 新增：SQL 控制台结果集导出（CSV / JSON / Excel 三格式，流式导出大结果集，强制只读）

## 迭代目标

为 SQL 查询控制台补充结果集导出能力。执行 SELECT 后，用户可在结果区域点击「导出」按钮
选择 CSV / JSON / Excel 格式下载当前 SQL 的完整结果集。导出强制只读（仅允许
SELECT/WITH/SHOW/DESCRIBE/EXPLAIN），对所有角色生效——导出场景不应执行 DDL/DML。
CSV / JSON 走流式 `StreamingHttpResponse` 避免大结果集 OOM；Excel 用 openpyxl
`write_only` 模式逐行写入。

## 改动文件清单

### 修改

- `backend/apps/manager/query.py` — 新增 `iter_select_rows`（流式执行 SELECT，
  返回列名 + 行迭代器，复用 `_strip_sql`/`_is_read_only`，PG/MySQL 启用
  `stream_results`，`fetchmany(1000)` 分批）、`rows_to_json`（流式 JSON 数组生成器，
  首块 `[` + 首行，后续 `,\n` + 行，末块 `]`）、`export_sql_result_excel`
  （openpyxl write_only，sheet 名 `query_result`，强制 `read_only=True`）；
  导入 `itertools`；`__all__` 加入三个新函数
- `backend/apps/manager/schemas.py` — 新增 `SqlExportIn`（`sql: str` +
  `format: Literal["csv","json","xlsx"] = "csv"`）；导入 `Literal`；`__all__` 加入
- `backend/apps/manager/api.py` — 扩展 `_EXPORT_FORMATS` 加 `json`；新增
  `_TABLE_EXPORT_FORMATS`（csv/sql/xlsx）与 `_SQL_EXPORT_FORMATS`（csv/json/xlsx）
  显式允许列表；`export_table_view` 改用 `_TABLE_EXPORT_FORMATS`（json 不适用于表导出）；
  新增 `export_sql_result_view`（POST `/{ds_id}/query/export`，强制只读，CSV/JSON 流式、
  xlsx 一次性返回，失败记审计日志）；模块顶部 docstring 加新端点说明；导入新增的
  query 函数与 schema
- `frontend/src/types/index.ts` — 新增 `SqlExportFormat`（"csv"|"json"|"xlsx"）与
  `SqlExportRequest`
- `frontend/src/api/manager.ts` — 新增 `exportSqlResult(dsId, body)` 返回 Blob
- `frontend/src/pages/SqlConsole.tsx` — 结果元信息区新增「导出」Dropdown（CSV/JSON/Excel），
  仅 `columns.length > 0` 时显示；复用 Manager.tsx 的 `URL.createObjectURL` 下载逻辑

### 测试

- `tests/test_manager_query.py` — 新增 8 个 `iter_select_rows` 单测（含列名+行、空结果
  保留列名、小 batch_size、DDL/DML 拒绝、read_only=False 允许 SELECT、空 SQL、末尾分号）、
  5 个 `rows_to_json` 单测（基本数组、空结果、单行、None 值、中文 ensure_ascii=False）、
  5 个 `export_sql_result_excel` 单测（xlsx 解析、空结果保留表头、DDL/DML 拒绝、末尾分号）；
  导入 `Iterator` 与三个新函数
- `tests/test_manager_api.py` — 新增 12 个 `/query/export` 接口测试（CSV/JSON/xlsx 三格式
  200、默认 csv、viewer 可导出 SELECT、DDL/DML 返回 403、sql 格式 422、未认证 401、
  未知数据源 404、空 SQL 400、语法错误 400、空结果保留表头）

## 关键决策与依据

### 1. `iter_select_rows` 用共享 state 回填列名

需要返回 `(columns, rows_iter)` 供调用方在流式响应前构造 CSV 表头 / Excel 表头。
但 Python 生成器无法跨 `with` 退出存活——若在 `with` 内执行查询取列名，退出 `with`
后连接关闭，生成器无法继续 yield 行。

方案：定义生成器 `_stream()`，内部 `with engine.connect()` 执行查询，将列名写入共享
`state["columns"]` 字典，再 yield 行。函数体调用 `next(gen)` 触发生成器执行到首行
（或 StopIteration），此时 `state["columns"]` 已填充。首行用 `itertools.chain([first_row], gen)`
接回迭代器头部。这样调用方拿到返回值时列名已就绪，行迭代器包含全部行。

空结果集（`returns_rows=True` 但 0 行）时 `next(gen)` 抛 StopIteration，返回实际列名
与空迭代器——CSV/JSON 导出仍有表头/数组骨架但无数据行，符合预期。

### 2. 导出强制只读，对所有角色生效

与 `execute_sql_view` 不同（viewer 限只读、designer/admin 可写），导出场景对所有角色
强制 `read_only=True`：导出本就是只读操作，不应执行 DDL/DML。`iter_select_rows` 在
`read_only=True` 时复用 `_is_read_only` 判断前缀，非只读语句直接抛 `QueryError`，
API 层捕获后返回 403（与 `execute_sql_view` 的 viewer 越权一致）。

### 3. 表导出与 SQL 结果集导出的格式允许列表分离

`_EXPORT_FORMATS` 是共享的 mime 类型注册表（csv/json/sql/xlsx 四种）。但表导出
（`export_table_view`）支持 csv/sql/xlsx（有表名可生成 INSERT），SQL 结果集导出
（`export_sql_result_view`）支持 csv/json/xlsx（SELECT 无目标表，无法生成 INSERT）。
新增 `_TABLE_EXPORT_FORMATS` 与 `_SQL_EXPORT_FORMATS` 两个 frozenset 做显式允许列表，
避免 json 误用于表导出、sql 误用于 SQL 结果集导出。

### 4. format 校验返回 422 而非 400

`SqlExportIn.format` 用 `Literal["csv","json","xlsx"]`，传 `format: "sql"` 时 Pydantic
在 ninja 层校验失败返回 422（Unprocessable Entity），不会到达视图内手写的 400 分支。
这是 body 校验失败的标准行为（与 query 参数的字符串校验不同），测试期望 422。

## 整合优化情况

- 复用 `iter_table_rows` / `rows_to_csv` / `export_excel` 的流式模式与 `_format_excel_value`
  等辅助函数，未重复造轮子。
- `_strip_sql` / `_is_read_only` 在 `execute_sql` / `iter_select_rows` 共用，行为一致。
- 前端下载逻辑复用 Manager.tsx 的 `URL.createObjectURL` + `<a>` 点击模式。

## 测试验证结果

- ruff check / format：全绿
- pyrefly：0 errors
- pytest：910 passed，覆盖率 97.82%（≥ 95% 门禁）
- 前端 `tsc --noEmit`：0 errors
- 新增覆盖：`iter_select_rows` 列名回填与只读拦截、`rows_to_json` 流式 JSON 数组、
  `export_sql_result_excel` 强制只读与表头保留、`/query/export` 三格式 200 与各类错误码

## 遗留事项

- `query.py` 第 1019 行（`if not result.returns_rows: return`）未覆盖：read_only=True
  时 SELECT 必返回行，此分支为防御性代码，99% 覆盖率下可接受。
- 导出按钮仅在结果区域 `columns.length > 0` 时显示（DDL/DML 无结果集不显示），
  与设计一致。

## 下一轮计划

进入 iter-23：SQL 历史与快捷执行（前端 localStorage 持久化 + Ctrl+Enter 快捷执行 +
选中片段执行 + SELECT 自动 LIMIT 保护）。
