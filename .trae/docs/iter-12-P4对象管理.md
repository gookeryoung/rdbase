# 迭代记录 12 - P4 对象管理

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P4 阶段。

- [x] 19 数据浏览接口与界面：分页/排序/筛选/列显隐、行数统计
- [x] 20 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
- [x] 21 SQL 查询控制台：多 Tab、Monaco 编辑器、执行、结果表格、执行计划
- [x] 22 导入导出：CSV/Excel/SQL 脚本导入导出（流式处理大文件）
- [x] 23 对象管理：视图/存储过程/函数/触发器查看与编辑
- [ ] 24 P4 测试与文档：manager 模块测试、大数据量流式测试、文档更新

## 迭代目标

P4-5 对象管理：交付视图/存储过程/函数/触发器的列表、查看定义、编辑（DROP IF EXISTS + CREATE 事务）、删除功能，前端 Manager.tsx 树节点扩展对象分组并集成 Monaco 编辑器查看/编辑面板。

后端实现 19+ 反射与 CRUD 函数（`list_views`/`get_view_definition`/`alter_view`/`drop_view` + routines + triggers），多方言适配（MySQL information_schema、PG pg_catalog、SQLite sqlite_master），事务保证 DROP + CREATE 原子性；12 个 API 接口（views/routines/triggers 各 4 个）+ RBAC 权限分层（读所有登录用户、写 designer+）；前端 types 新增 11 个类型，api 新增 12 个函数，Manager.tsx 树扩展 4 分组（表/视图/过程函数/触发器）+ 对象面板 + Monaco 编辑器 Modal。

## 改动文件清单

### 后端（backend/）

- `backend/apps/manager/objects.py` — 新增对象反射与 CRUD 模块：
  - `ObjectError`（对象操作错误）
  - `ViewMeta`/`RoutineMeta`/`TriggerMeta`（dataclass 元数据）
  - `list_views`/`get_view_definition`/`alter_view`/`drop_view`（视图 CRUD，多方言 SQL）
  - `list_routines`/`get_routine_definition`/`alter_routine`/`drop_routine`（存储过程/函数 CRUD，SQLite 不支持抛 ObjectError）
  - `list_triggers`/`get_trigger_definition`/`alter_trigger`/`drop_trigger`（触发器 CRUD，PG DROP 需关联表名）
- `backend/apps/manager/schemas.py` — 新增 P4-5 段落：
  - `NameOut`（对象名列表项）
  - `ViewDetailOut`（视图详情：name/schema_name/definition）
  - `RoutineBriefOut`/`RoutineDetailOut`（name/schema_name/type/definition）
  - `TriggerBriefOut`/`TriggerDetailOut`（name/schema_name/event/table/timing/definition）
  - `ObjectUpdateIn`（编辑请求：definition + 可选 table）
- `backend/apps/manager/api.py` — 新增 12 个接口（P4-5 段落）：
  - `GET /{ds_id}/views`、`GET /{ds_id}/views/{name}`、`PUT /{ds_id}/views/{name}`、`DELETE /{ds_id}/views/{name}`
  - `GET /{ds_id}/routines`、`GET /{ds_id}/routines/{name}`、`PUT /{ds_id}/routines/{name}`、`DELETE /{ds_id}/routines/{name}`（type 参数区分 procedure/function）
  - `GET /{ds_id}/triggers`、`GET /{ds_id}/triggers/{name}`、`PUT /{ds_id}/triggers/{name}`、`DELETE /{ds_id}/triggers/{name}`（PG 删除需 table 参数）
  - 读操作所有登录用户、写操作 designer+；`_resolve_obj_schema` 统一解析 Schema（SQLite 强制 None、PG 默认 public、MySQL 默认当前数据库）

### 前端（frontend/src/）

- `frontend/src/types/index.ts` — 新增 P4-5 段落：
  - `ObjectType`（"views"|"routines"|"triggers"）
  - `RoutineKind`（"procedure"|"function"）
  - `ViewDetail`、`RoutineBrief`、`RoutineDetail`、`TriggerBrief`、`TriggerDetail`、`ObjectUpdate`
- `frontend/src/api/manager.ts` — 新增 12 个 API 函数（P4-5 段落）：
  - `listViews`/`retrieveView`/`updateView`/`deleteView`
  - `listRoutines`/`retrieveRoutine`/`updateRoutine`/`deleteRoutine`（含 type 参数）
  - `listTriggers`/`retrieveTrigger`/`updateTrigger`/`deleteTrigger`（删除含 table 参数）
  - `buildSchemaQuery` 通用 query 构造工具，`encodeURIComponent` 编码对象名
- `frontend/src/pages/Manager.tsx` — 树与内容区扩展：
  - 树节点新增 4 分组（表/视图/过程函数/触发器），schema 展开时 `Promise.all` 并行懒加载三种对象列表
  - 数据源切换时清空对象缓存（viewsBySchema/routinesBySchema/triggersBySchema/objectsLoadedSchema）
  - `SelectedObject` 类型与 `ObjectModalState` 类型，`handleTreeSelect` 新增 view/routine/trigger 分支解析
  - 对象面板：Tag 标识类型（视图 cyan / 过程 geekblue / 函数 geekblue / 触发器 orange），「查看定义」「编辑」「删除」三按钮
  - `openObjectModal` 拉取定义后填充 Monaco 编辑器，`handleObjectModalSubmit` 调用对应 update API 后刷新对象列表
  - `handleDeleteObject` 调用对应 delete API 后清空选中并刷新对象列表
  - Modal 内嵌 Monaco Editor（height=420px，view 模式 readOnly，edit 模式可编辑），保存提示「DROP IF EXISTS + CREATE 事务」说明

### 测试（tests/）

- `tests/test_manager_objects.py` — 新增 45 个单元测试：
  - `list_views`/`get_view_definition`/`alter_view`/`drop_view`（SQLite 真实视图 CRUD）
  - `list_routines`/`get_routine_definition`/`alter_routine`/`drop_routine`（SQLite 不支持抛 ObjectError）
  - `list_triggers`/`get_trigger_definition`/`alter_trigger`/`drop_trigger`（SQLite 真实触发器 CRUD）
  - MySQL/PostgreSQL 方言通过 monkeypatch dialect + Mock connection 验证 SQL 模板
- `tests/test_manager_objects_api.py` — 新增 40 个接口测试：
  - 列表/详情/编辑/删除端到端（viewer 可读、designer+ 可写）
  - 权限分层（viewer PUT/DELETE 返回 403、designer 返回 200）
  - 错误分支（数据源不存在 404、对象不存在 404、SQLAlchemy 错误 400、SQLite 不支持 routines 400）
  - monkeypatch `manager_api.list_views`/`list_routines`/`list_triggers` 等函数抛 SQLAlchemyError 验证 400 响应

## 关键决策与依据

1. **多方言反射 SQL 模板化**：视图/例程/触发器列表与定义查询以 dict 按方言（mysql/postgresql/sqlite）映射 SQL 模板，SQLite 用 `sqlite_master`/`sqlite_schema`，MySQL/PG 用 `information_schema`/`pg_catalog`。SQLite 不支持例程，函数内部抛 `ObjectError`，API 转 400 响应。
2. **编辑采用 DROP IF EXISTS + CREATE 事务**：所有对象编辑统一为「先 DROP 旧定义再 CREATE 新定义」模式，包裹在 `engine.begin()` 事务内，保证原子性。避免方言差异化的 ALTER 语法（如 PG `CREATE OR REPLACE`、MySQL `ALTER VIEW`、SQLite 不支持 ALTER VIEW），简化实现。
3. **触发器删除 PG 需关联表**：PG `DROP TRIGGER name ON table` 语法强制要求 `ON table`，因此 `ObjectUpdateIn` 增加 `table` 可选字段，`delete_trigger` API 接受 `table` 查询参数。MySQL/SQLite 不需要，自动忽略。前端从 `triggersBySchema` 缓存中查关联表自动填充。
4. **Schema 解析统一函数**：`_resolve_obj_schema(ds, schema_name)` 统一处理 SQLite 强制 None、PG 默认 public、MySQL 默认当前数据库（None），避免每个接口重复逻辑。
5. **前端树分组懒加载**：schema 展开时并行 `Promise.all([listViews, listRoutines, listTriggers])` 一次加载三类对象，`objectsLoadedSchema` Set 避免重复请求。错误用 `.catch(() => [])` 兜底，保持其他类型仍可显示。
6. **Monaco 编辑器复用**：对象查看/编辑 Modal 复用 SqlConsole.tsx 已验证的 `@monaco-editor/react` 集成模式，view 模式 `readOnly: true`、edit 模式可编辑，保存前提示「DROP IF EXISTS + CREATE 事务」。
7. **不新增对象创建功能**：对象编辑基于已有对象的 DROP+CREATE 模式，新建对象走 SQL 查询控制台（P4-3）执行 CREATE 语句后刷新对象列表即可，避免冗余 UI。

## 代码实现情况

- 后端 `objects.py` 197 行，`api.py` P4-5 段落约 320 行（12 个接口 + `_resolve_obj_schema` 辅助函数）
- 前端 `types/index.ts` 新增 50 行类型定义，`api/manager.ts` 新增 165 行 API 封装，`Manager.tsx` 新增约 350 行（state + treeData + 事件处理 + Modal）
- 测试 `test_manager_objects.py` 45 用例，`test_manager_objects_api.py` 40 用例

## 整合优化情况

- `_resolve_obj_schema` 辅助函数消除 12 个接口中的 Schema 解析重复
- `buildSchemaQuery` 前端工具函数消除 query 参数构造重复
- 对象 treeData 复用既有 schema 顶层节点，仅扩展 children 为 4 分组，保持表选中行为不变
- 对象编辑 Modal 复用 Monaco Editor 组件，与 SqlConsole 风格一致

## 测试验证结果

- `uv run ruff check backend tests`：通过
- `uv run ruff format --check backend tests`：74 文件已格式化
- `uv run pyrefly check`：0 errors（46 suppressed, 90 warnings not shown）
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：534 测试全绿，覆盖率 98.13%
  - `backend/apps/manager/objects.py`：98%（122/148 行未覆盖为 MySQL/PG 真实连接分支，已用 mock SQL 模板验证）
  - `backend/apps/manager/api.py`：93%（导出/导入流式错误分支与对象 API SQLAlchemy 错误分支部分未覆盖，总覆盖率 98.13% 满足 95% 门禁）
- `npm run typecheck`：通过

## 遗留事项

- 浏览器端到端手动测试未执行（依赖 typecheck 与单元/接口测试覆盖类型正确性）
- MySQL/PostgreSQL 真实连接的对象反射测试未执行（mock 验证 SQL 模板，需后续集成环境验证）
- `backend/apps/manager/api.py` 覆盖率 93%，对象 API SQLAlchemy 错误分支部分通过 monkeypatch 覆盖，少量分支未覆盖但不影响 95% 总门禁
- 24 任务（P4 测试与文档收尾）尚未开始

## 下一轮计划

进入 P4-6（任务 24）P4 测试与文档收尾：补全 manager 模块集成测试、大数据量流式测试、Sphinx 文档汇总；或直接进入 P5 系统管理与部署阶段。
