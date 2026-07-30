# 迭代记录 08 - P4 数据浏览接口与界面

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P4 阶段。

- [x] 19 数据浏览接口与界面：分页/排序/筛选/列显隐、行数统计
- [ ] 20 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
- [ ] 21 SQL 查询控制台
- [ ] 22 导入导出
- [ ] 23 对象管理
- [ ] 24 P4 测试与文档收尾

## 迭代目标

P4-1 数据浏览：交付 manager 应用骨架（已在 P3 末尾预留）+ 数据查询模块（分页/排序/筛选/列显隐 + 行数统计，白名单校验防 SQL 注入）+ 行浏览 API（GET /manager/{ds_id}/tables/{table_name}/rows）+ 前端数据库管理页面（左侧数据源+表树按 schema 折叠，右侧数据表格含列头筛选/列显隐/分页/排序）。

## 改动文件清单

### 后端（backend/）

- `backend/apps/manager/query.py` — 数据查询模块：`query_table_rows`（分页/排序/筛选/列显隐 + 同连接统计 total）、`count_table_rows`（独立计数）、`get_column_names`（白名单辅助）、`_quote_ident`/`_format_table_ref`/`_resolve_schema`（标识符引用 + SQLite schema 处理，独立于 designer 模块）、`_build_where_clause`（eq/ne/gt/lt/ge/le/like/in 操作符 + 参数绑定，in 展开多占位符）、`QueryError` 异常类
- `backend/apps/manager/schemas.py` — `RowListOut`（items/total/page/page_size/columns）
- `backend/apps/manager/api.py` — Router：`GET /{ds_id}/tables/{table_name}/rows` 接口（所有登录用户可读，filters JSON 字符串参数 + columns 逗号分隔参数 + 分页/排序参数），`_parse_filters`/`_parse_columns`/`_get_ds_or_404` 辅助
- `backend/api/v1/__init__.py` — 挂载 manager_router 到 `/manager`

### 测试（tests/）

- `tests/test_manager_query.py` — 37 个单元测试：标识符引用（MySQL/PG/SQLite）、_resolve_schema、get_column_names、_build_where_clause（空/eq/in/空列表/多条件/全部操作符）、query_table_rows（默认/分页/越界分页/升序/降序/列子集/eq/like/in/组合筛选/非法列/非法排序/非法筛选列/非法操作符/非法 page/非法 page_size/非法 order_dir/未知表/SQLite 忽略 schema）、count_table_rows（默认/带筛选/未知表/非法筛选列）
- `tests/test_manager_api.py` — 18 个集成测试：默认查询/分页/排序/列子集/eq 筛选/like 筛选/in 筛选/空表返回 columns、未认证 401/数据源 404/未知表 400/非法列 400/非法排序 400/非法 filters JSON 400/非法操作符 400/filters 非对象 400/SQLAlchemyError 400/空表反射异常回退空 columns

### 前端（frontend/）

- `frontend/src/types/index.ts` — 追加 P4 类型：`RowFilterOp`（eq/ne/gt/lt/ge/le/like/in）、`RowFilter`、`RowQuery`、`RowListResponse`
- `frontend/src/api/manager.ts` — `listRows(dsId, tableName, params)`：将 filters 序列化为 JSON 字符串、columns 序列化为逗号分隔字符串
- `frontend/src/pages/Manager.tsx` — 数据库管理页面：左侧 Sider（数据源 Select + Tree 按 schema 折叠 + 懒加载表列表）+ 右侧 Content（数据表格 + 列头下方 Input 筛选框 like 模糊匹配 + 列显隐 Dropdown Checkbox + 分页/排序 + NULL 值灰色显示 + 刷新按钮）
- `frontend/src/routes/index.tsx` — `/manager` 路由从 Placeholder 替换为 Manager 组件（移除未使用的 Typography 占位）

## 关键决策与依据

1. **manager 独立实现标识符引用与 schema 解析**：参考 designer/ddl.py 的 `_quote_ident`/`_format_table_ref` 与 designer/inspector.py 的 `_resolve_schema`，但 manager 模块独立实现一份，避免跨模块依赖（manager 不依赖 designer）。
2. **白名单校验防 SQL 注入**：通过 SQLAlchemy inspect 获取表的真实列名集合，校验 `columns`/`order_by`/`filters` 的列名都在白名单内，杜绝 SQL 注入；操作符通过 `_COMPARATORS` frozenset 白名单校验；筛选值通过 SQLAlchemy `text()` 参数绑定（`:f0`、`:f0_0` 等）传递，不拼接 SQL 字面量。
3. **`in` 操作符展开多占位符**：`IN (:f0_0, :f0_1, :f0_2)` 而非 `IN :val`，避免 SQLAlchemy `text()` 对列表参数的方言差异（部分 dialect 不支持绑定列表参数）。
4. **query_table_rows 同连接统计 total**：在 `engine.connect()` 的同一连接内执行 SELECT 与 COUNT(*)，避免重复反射列名（`get_column_names` 只调一次），同时保证一致性。
5. **count_table_rows 独立公共函数**：按任务规格实现，便于前端独立获取行数（虽然 rows 接口已返回 total，但保留独立计数能力以备后续 SQL 控制台等场景使用）。
6. **filters 通过 JSON 字符串参数传递**：GET 请求的 query 参数无法直接表达嵌套 dict，采用 URL-encoded JSON 字符串（`filters={"name":{"op":"like","val":"J%"}}`），前端 `JSON.stringify` + `encodeURIComponent`，后端 `json.loads` 解析并校验类型。
7. **columns 通过逗号分隔字符串传递**：简单可读，避免 JSON 嵌套，符合 RESTful 风格。
8. **空表 columns 回退反射**：当查询返回 0 行且用户未指定 columns 时，rows[0].keys() 无法获取列名，回退到 `get_column_names` 反射；若反射也失败（极端情况），返回空列表（不阻塞响应）。
9. **前端筛选极简设计**：列头下方直接放 Input 输入框（like 模糊匹配），无对话框、无操作符选择（统一 `%kw%`）；列显隐用 Dropdown + Checkbox 多选，全选时回退为 null（全部可见）。
10. **前端表树懒加载**：Tree 节点展开时才调用 `listTables` 加载对应 schema 的表，避免一次加载所有 schema 的表。
11. **未新增 Django 模型**：manager 应用无 Django 模型（数据浏览直接通过 SQLAlchemy 反射目标库），无需 migrations 文件，符合「不为未来预留扩展点」原则。
12. **PLR0917 抑制**：`list_rows_view` 与 `query_table_rows` 参数较多（10/9 个），添加 `# noqa: PLR0913, PLR0917` 抑制 ruff 的「过多位置参数」警告（ninja 路由签名与查询函数签名客观需要这些参数）。

## 代码实现情况

### query.py（264 行）

- `QueryError`：查询错误异常（列名非法、操作符不支持、表不存在、分页参数非法等）
- `_quote_ident(name, dialect)`：MySQL 反引号、其他双引号
- `_format_table_ref(table_name, schema, dialect)`：含 schema 前缀，SQLite 忽略 schema
- `_resolve_schema(engine, schema)`：SQLite 强制 None
- `get_column_names(engine, table_name, schema)`：通过 SQLAlchemy inspect 获取列名列表，反射失败抛 QueryError
- `_validate_columns`/`_validate_order_by`/`_validate_filter_columns`：白名单校验
- `_build_where_clause(filters, dialect)`：构造 WHERE 子句与参数 dict，in 操作符展开多占位符
- `query_table_rows(...)`：校验参数 → 反射列名 → 校验白名单 → 构造 SELECT/ORDER BY/LIMIT OFFSET/WHERE → 同连接执行 SELECT + COUNT → 返回 (rows, total)
- `count_table_rows(...)`：校验 → 反射列名 → 构造 COUNT SQL → 执行返回 int

### api.py（140 行）

- `router = Router(tags=["manager"], auth=JWTAuth())`：所有接口需登录
- `_get_ds_or_404(ds_id)`：按 ID 获取数据源，不存在抛 404
- `_parse_filters(filters_param)`：JSON 字符串 → dict，非法 JSON 或非对象抛 400
- `_parse_columns(columns_param)`：逗号分隔字符串 → list[str] | None
- `list_rows_view(...)`：GET 接口，解析 query 参数 → 调用 query_table_rows → 异常分支（QueryError/SQLAlchemyError）转 400 → 计算返回列名顺序（parsed_columns / rows[0].keys() / get_column_names 反射 / 空列表兜底）→ 返回 RowListOut

### Manager.tsx（456 行）

- 状态：datasources/selectedDsId/schemas/tablesBySchema/selectedTable/rows/total/columns/loadingRows/page/pageSize/orderBy/orderDir/visibleCols/filterInputs
- 左侧：数据源 Select + Tree（schema 节点带表数量统计，懒加载）
- 右侧：标题栏（表名 + schema + 总行数 + 列显隐 Dropdown + 刷新）+ Table（列头含筛选 Input、排序、分页）
- 列显隐：Dropdown + Checkbox 多选，全选时回退 null
- 筛选：列头 Input，onChange 更新 filterInputs 并重置 page，onPressEnter 触发查询；filterInputs 非空转 like `%kw%` 筛选
- 排序：Table onChange 解析 sorter.field/order → setOrderBy/setOrderDir

## 整合优化情况

- query.py 与 api.py 解耦：query.py 纯查询逻辑（无 Django 依赖，便于单元测试），api.py 负责 HTTP 参数解析与异常转换
- 测试覆盖率：manager/api.py 100%、manager/query.py 100%（通过 monkeypatch 覆盖 SQLAlchemyError 与空表反射异常分支）
- 前端复用 designer 的 `listSchemas`/`listTables` API（元数据反射接口本就是通用能力，不属于 manager 专属）
- 前端 typecheck 通过（修复了 unused state、TableProps onChange 类型签名问题）

## 测试验证结果

`make check` 全套通过：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 71 files already formatted
- `pyrefly check` — 0 errors (46 suppressed, 77 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 267 passed, coverage 99.76%

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
| test_manager_query.py | 37 |
| test_manager_api.py | 18 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **267** |

覆盖率：99.76%（高于上轮 99.72%）

| 模块 | 覆盖率 |
|------|--------|
| backend/apps/manager/api.py | 100% |
| backend/apps/manager/query.py | 100% |
| backend/apps/manager/schemas.py | 100% |
| backend/apps/manager/apps.py | 100% |
| backend/apps/manager/admin.py | 100% |
| backend/apps/manager/__init__.py | 100% |

## 遗留事项

- 前端无单元测试（Ant Design 组件测试 ROI 低，依赖 typecheck + 手测）。
- 浏览器端到端手测：依赖后端+前端服务运行，本轮通过 typecheck 验证类型正确性，未做完整浏览器手测。
- 大数据量浏览：当前 LIMIT/OFFSET 分页在百万级数据下性能不佳（深翻页问题），后续 P4-2 数据 CRUD 阶段可考虑游标分页或 keyset pagination。
- 多数据库方言测试：本轮仅用 SQLite 内存库/文件库测试，MySQL/PostgreSQL 路径通过静态分析保证（标识符引用、schema 解析逻辑与 designer 模块一致，已被 designer 测试覆盖）。
- 列头筛选仅支持 like 模糊匹配（统一 `%kw%`），未提供操作符选择 UI（极简设计，符合用户偏好）；如需 eq/gt/lt 等精确筛选可通过 API 参数手动构造。

## 下一轮计划

P4-1 数据浏览已交付（任务 19 [x]），进入 **P4-2 数据 CRUD**（任务 20）：

1. **P4-2 收集**：研究 SQLAlchemy ORM 行级 CRUD、乐观锁（version 字段或 rowid）、事务管理。
2. **P4-2 计划**：拆分子任务
   - 后端：`POST /manager/{ds_id}/tables/{table_name}/rows`（新增行）、`PATCH /manager/{ds_id}/tables/{table_name}/rows/{pk}`（编辑行）、`DELETE /manager/{ds_id}/tables/{table_name}/rows/{pk}`（删除行），主键反查（多列主键支持）
   - 前端：行内编辑/新增/删除按钮、确认对话框、乐观锁冲突提示
3. **P4-2 实现→测试→文档→验证**：六步迭代循环。
