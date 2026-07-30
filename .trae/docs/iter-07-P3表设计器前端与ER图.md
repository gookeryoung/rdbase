# 迭代记录 07 - P3 表设计器前端与 ER 图

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P3 阶段。

- [x] 14 元数据反射：基于 SQLAlchemy inspect 提供库/Schema/表/字段元数据读取
- [x] 15 表设计器后端：字段 Schema、DDL 生成器（CREATE/ALTER 多方言）、设计草稿模型与版本
- [x] 16 表设计器前端：字段编辑表格、类型选择、索引面板、DDL 预览
- [x] 17 关系设计与 ER 图：外键/多对多配置、React Flow ER 图、连线同步 DDL
- [x] 18 P3 测试与文档收尾

## 迭代目标

P3 数据库设计里程碑（前端部分）：交付表设计器前端（字段/索引/外键编辑表格 + DDL 预览 + 版本回滚）与 React Flow ER 图（节点=表、连线=外键、拖拽创建/删除外键同步 spec），完成 P3 全部子任务。

覆盖迭代 06（后端）+ P3-3（前端表设计器）+ P3-4（ER 图）+ P3-5（收尾）。

## 改动文件清单

### 前端（frontend/）

- `frontend/src/types/index.ts` — P3 类型定义：DraftStatus/FieldSpec/IndexSpec/ForeignKeySpec/TableDesignSpec/Draft/DraftCreate/DraftUpdate/Version/DDLPreviewRequest/DDLResult/DDLExecuteRequest/NameItem/TableBrief/ColumnMeta/IndexMeta/ForeignKeyMeta/TableDetail
- `frontend/src/api/designer.ts` — designer API client：反射接口（listDatabases/listSchemas/listTables/listViews/retrieveTable）+ 草稿 CRUD（listDrafts/createDraft/retrieveDraft/updateDraft/deleteDraft）+ 版本管理（listVersions/rollbackToVersion）+ DDL 预览/执行（previewDDL/applyDraft）
- `frontend/src/pages/Designer.tsx` — 设计器主页面：左侧草稿列表（单选/新建/删除）+ 右侧 Tabs（字段编辑表格/索引面板/外键面板/ER 图/DDL 预览/版本回滚）+ 顶部保存/应用按钮 + 新建草稿 Modal
- `frontend/src/components/ERGraph.tsx` — React Flow ER 图组件：自定义 TableNode（表名头 + 字段列表，每行带左右 Handle）+ 边映射 spec.foreign_keys + 拖拽 onConnect 创建外键 + onEdgesDelete 移除外键

### 后端（无变更）

P3-3/P3-4 阶段后端无变更，全部复用 iter-06 交付的反射/草稿/版本/DDL 接口。

## 关键决策与依据

1. **类型镜像后端 Schema**：`types/index.ts` 中的 FieldSpec/IndexSpec/ForeignKeySpec/TableDesignSpec 与后端 `apps/designer/schemas.py` 字段一一对应，避免类型漂移。
2. **草稿编辑本地状态 + 显式保存**：选中草稿时 `structuredClone(spec)` 初始化 editingSpec，所有编辑操作更新本地状态，点击「保存」才 PATCH 到后端并自动创建新版本。避免每次按键触发请求，符合「极简设计」原则。
3. **DDL 预览懒生成**：编辑时清空 ddlStatements（setDdlStatements(null)），点击「生成 DDL」按钮才调用后端 previewDDL 接口，避免编辑过程中频繁请求。
4. **ER 图节点 = 同数据源草稿表**：通过 `drafts.filter(d => d.datasource_id === currentDraft.datasource_id)` 过滤可见节点，避免跨数据源混淆。当前编辑表用蓝色边框 + "编辑中" Tag 高亮。
5. **ER 图拖拽限制**：onConnect 时校验 connection.source 必须等于当前编辑草稿的节点 ID，否则提示「只能在当前编辑的表上创建外键」。避免误修改其他草稿 spec。
6. **外键边与 spec.foreign_keys 索引一一对应**：edge.id = `e_<fkIndex>`，删除边时按 fkIndex 降序移除（避免索引错位）。
7. **节点位置内存维护**：visibleDrafts 变化时通过 `prevPos` Map 复用已拖拽位置，但不持久化到后端（极简设计，避免增加后端存储字段）。
8. **字段 Handle ID 编码**：source handle = `s_<fieldName>`，target handle = `t_<fieldName>`，节点 ID = `t_<draftId>`，便于 onConnect 时反查。
9. **权限分层**：`canEdit = isDesignerOrAdmin(user)`，viewer 只读（隐藏新建/保存/应用按钮，ER 图 nodesConnectable=false）。
10. **Ant Design 风格**：Table 可编辑行用 Input/Select/InputNumber/Switch 直接渲染在单元格内，避免 Cell 组件抽象。索引/外键列用 `mode="tags"` 的 Select 多选列。

## 代码实现情况

### Designer.tsx（1179 → 1232 行）

- 状态管理：drafts/datasources/selectedId/editingSpec/editingName/saving/applying/createOpen/ddlStatements/ddlLoading/versions/activeTab
- 编辑回调：updateField/addField/removeField、updateIndex/addIndex/removeIndex、updateForeignKey/addForeignKey/addForeignKeyFromER/removeForeignKey
- 业务回调：handleSave（PATCH 草稿 + 自动创建版本）/handleApply（POST apply 执行 DDL）/handlePreviewDDL/handleDelete/handleRollback/handleCreate
- Tabs：字段 / 索引 / 外键 / ER 图 / DDL 预览 / 版本

### ERGraph.tsx（287 行）

- TableNode 自定义节点：表名头（当前编辑表蓝色背景）+ 字段列表（主键蓝色加粗 + 类型灰色辅助文本）+ 每行左右 Handle
- layoutNodes：网格布局（3 列 × N 行，间距 320×360）
- initialEdges：基于 currentDraft.spec.foreign_keys 生成边，源节点 = 当前草稿，目标节点 = 通过 referred_table 反查 draft
- onConnect：解析 connection.source/sourceHandle/target/targetHandle → 构造 ForeignKeySpec（columns=[src], referred_table=target.table_name, referred_columns=[tgt], on_delete='RESTRICT'）→ onAddForeignKey 回调
- onEdgesDelete：解析 edge.id 反推 fkIndex → 降序删除避免索引错位
- ReactFlowProvider 包裹：保证 useNodesState/useEdgesState 在同一 React Flow 上下文

## 整合优化情况

- ERGraph 作为独立组件，避免污染 Designer.tsx；通过 props（drafts/currentDraft/canEdit/onAddForeignKey/onRemoveForeignKey）解耦。
- 节点位置用 `prevPos` Map 复用，避免 visibleDrafts 变化时重置布局。
- 边的 useEffect 同步：`setEdges(initialEdges)` 在 spec.foreign_keys 变化时重建，保证 ER 图与「外键」Tab 数据一致。
- 拖拽创建外键后立即在「外键」Tab 中可见（共享 editingSpec 状态），无需额外同步逻辑。

## 测试验证结果

`make check` 全套通过（无后端变更，验证无回归）：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 62 files already formatted
- `pyrefly check` — 0 errors (44 suppressed, 71 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 212 passed, coverage 99.72%

前端门禁：

- `npm run typecheck` — 通过（tsc --noEmit 0 错误）

测试分布（与 iter-06 一致，无新增后端测试）：

| 文件 | 用例数 |
|------|--------|
| test_accounts_api.py | 21 |
| test_accounts_flow.py | 3 |
| test_accounts_models.py | 6 |
| test_accounts_permissions.py | 12 |
| test_accounts_users.py | 18 |
| test_datasources_api.py | 19 |
| test_datasources_crypto.py | 6 |
| test_datasources_engine.py | 12 |
| test_datasources_models.py | 7 |
| test_designer_api.py | 15 |
| test_designer_ddl.py | 42 |
| test_designer_drafts_api.py | 29 |
| test_designer_inspector.py | 14 |
| test_accounts_jwt.py | 5 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **212** |

## 遗留事项

- 前端无单元测试（Ant Design + React Flow 组件测试 ROI 低，依赖 typecheck + 手测）。
- ER 图节点位置未持久化（极简设计，后续如需可加 `position` 字段到 DesignDraft.spec）。
- 多对多关系：当前 ER 图仅支持外键（一对多），多对多需通过中间表 + 两个外键实现，未单独提供"多对多配置"UI（与 ER 图节点+连线模式重叠，避免冗余）。
- 一对一关系：通过外键 + UNIQUE 约束实现，未单独提供 UI（同上）。
- 浏览器端到端手测：依赖后端+前端服务运行，本轮通过 typecheck 验证类型正确性，未做完整浏览器手测。

## 下一轮计划

P3 数据库设计里程碑全部交付完毕（任务 14-18 全部 [x]），进入 **P4 数据库管理**：

1. **P4-1 收集**：扫描需求清单中 P4 相关需求（任务 19-24）；研究 SQLAlchemy 流式查询、Monaco Editor 集成、CSV/Excel 流式导入导出模式。
2. **P4-1 计划**：拆分子任务
   - 数据浏览接口：分页/排序/筛选/列显隐、行数统计
   - 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
   - SQL 查询控制台：Monaco 编辑器、多 Tab、执行、结果表格、执行计划
   - 导入导出：CSV/Excel/SQL 脚本流式处理
   - 对象管理：视图/存储过程/函数/触发器查看与编辑
3. **P4-1 实现→测试→文档→验证**：六步迭代循环。
