# iter-23 SQL 历史与快捷执行

## 需求清单

- [x] 新增：SQL 控制台 Tab 内容与历史执行记录按数据源持久化到 localStorage
- [x] 新增：Ctrl/Cmd + Enter 快捷执行当前 Tab SQL
- [x] 新增：选中片段执行（选区非空时执行选中部分而非全文）
- [x] 新增：SELECT 自动 LIMIT 保护（无 LIMIT 的 SELECT 执行前询问是否追加 LIMIT 1000）

## 迭代目标

为 SQL 查询控制台补充「历史与快捷执行」能力。本轮纯前端改动，无后端变更。
解决四个用户痛点：刷新页面丢失 SQL 草稿、必须点按钮才能执行、长 SQL 只能整段执行、
误执行无 LIMIT 的全表扫描。

## 改动文件清单

### 修改

- `frontend/src/pages/SqlConsole.tsx` — 新增四个子功能（详见下方关键决策）：
  - localStorage 持久化：`safeRead`/`safeWrite` 工具函数、`tabsStorageKey`/`historyStorageKey`
    按数据源 ID 分键、`loadPersistedTabs`/`loadPersistedHistory` 加载、三个 useEffect
    负责加载与持久化
  - Ctrl+Enter 快捷执行：`handleEditorMount` 注册 `editor.addCommand`，通过
    `tabsRef`/`handleExecuteRef` 避免闭包过期
  - 选中片段执行：`handleExecute` 调用 `editor.getSelection()` + `getModel().getValueInRange()`
    取选中文本，非空则执行选中部分
  - SELECT 自动 LIMIT 保护：`isSelectWithoutLimit` 正则检测、`appendLimit` 追加、
    `Modal.confirm` 询问用户、「LIMIT 保护」开关
  - 历史面板：`buildHistoryMenu` 构建 Dropdown 菜单，点击条目回填到当前 Tab，
    含「清空历史」选项
  - `PersistedTab`/`SqlHistoryEntry`/`SelectionLike`/`MonacoEditorRef` 类型定义

## 关键决策与依据

### 1. 持久化用 plain localStorage，不引入 zustand persist 中间件

项目 `store/auth.ts` 与 `api/client.ts` 均直接用 `localStorage` API（getItem/setItem/
removeItem），未使用 zustand persist 中间件。遵循既有约定，新增 `safeRead`/`safeWrite`
封装（带 try/catch 容错），键名 `rdbase:sqlTabs:{dsId}` 与 `rdbase:sqlHistory:{dsId}`
按数据源 ID 分键，与 `rdbase_token`/`rdbase_user` 命名风格一致。

### 2. 持久化仅存 SQL 内容，不存执行结果

`PersistedTab` 只保留 `{ key, title, sql }`，不存 `result`/`explain`/`loading`/`error`。
原因：执行结果体积大（可能含上千行数据）、时效性强（数据源数据随时变化）、
刷新后重跑才有意义。Tab 的 key 与 title 一并持久化，刷新后 Tab 标识与名称保持稳定。

历史记录 `SqlHistoryEntry` 只存 `{ sql, executedAt, success }`，最多保留 50 条（FIFO），
不存执行结果或错误详情——历史用于「回填 SQL 重跑」，错误详情当时已通过 Alert 展示。

### 3. tabsDsIdRef 避免「切换数据源时旧 Tab 误存到新数据源键」

切换数据源时存在竞态：若保存 effect 依赖 `[selectedDsId, tabs]`，当 dsId 变化但 tabs
尚未被 load effect 替换时，保存 effect 会用新 dsId 写入旧 tabs，污染新数据源的存储。

方案：引入 `tabsDsIdRef`（在 load effect 中赋值为当前 dsId），保存 effect 仅依赖
`[tabs]` 并通过 ref 读取目标 dsId。React 18 effect 调度保证：dsId 变化的 commit 中
tabs 未变（保存 effect 不触发）；tabs 被 load effect 替换后的 commit 中 ref 已是正确
dsId（保存 effect 触发时写入正确键）。无竞态。

### 4. Ctrl+Enter 用 ref 持有最新 handleExecute，避免闭包过期

Monaco `editor.addCommand` 在 `onMount` 时注册一次，回调闭包捕获的是首次渲染的
`handleExecute`（含旧的 `selectedDsId`）。后续渲染 `selectedDsId` 变化后，快捷键仍调
旧闭包，执行到错误数据源。

方案：`handleExecuteRef` 与 `tabsRef` 通过无依赖 useEffect（每次渲染后执行）同步为
最新值，快捷键回调调用 `handleExecuteRef.current(tab)` 拿到最新闭包。按钮 onClick
直接用 `handleExecute(tab)`（按钮每次重渲染拿到最新闭包，无需 ref）。

### 5. 选中片段优先于全文

`handleExecute` 中：`editor.getSelection()` 非空则取 `getModel().getValueInRange(selection)`
作为待执行 SQL；选区为空时回退到 `editor.getValue()` 全文。`selectedText.trim()` 为空
（纯空白选区）也回退到全文，避免误执行空 SQL。

仅 `handleExecute` 支持选中片段；`handleExplain` 与 `handleExportSql` 仍用全文，
与规格一致（规格仅要求「选中片段执行」作用于执行）。

### 6. LIMIT 保护默认提示用户，可开关

规格要求「前端提示或自动追加 LIMIT 1000（可配置，默认提示用户）」。实现为：
- 「LIMIT 保护」Switch（默认开），位于 Tab 栏右侧
- 开启时：`isSelectWithoutLimit` 正则检测（去除 `--` 行注释与 `/* */` 块注释后，
  以 SELECT/WITH 开头且不含 `LIMIT \d+`），命中则弹 `Modal.confirm` 询问
  「追加并执行」或「原样执行」
- 关闭时：跳过检测，原样执行

`appendLimit` 去除末尾分号后追加 ` LIMIT 1000;`，保证语法正确。

### 7. .tsx 中泛型箭头函数用 `<T,>` 而非 `<T>`

`safeRead = <T>(...)` 在 `.tsx` 文件中会被 TS 解析器误判为 JSX 元素起始标签，
导致后续所有代码解析失败（上百个 TS1109/TS1381/TS1382 错误）。改为 `<T,>`（加逗号）
显式告知解析器这是泛型类型参数。这是 `.tsx` 文件的既定约定。

## 整合优化情况

- 复用 `store/auth.ts` 的 plain localStorage 模式（getItem/JSON.parse/setItem + try/catch），
  未引入 zustand persist 中间件，保持依赖与风格一致。
- `editorRefs` 既有引用机制扩展为 `MonacoEditorRef` 类型（新增 `getSelection`/`getModel`/
  `addCommand`），未另起引用存储。
- 历史记录 Dropdown 复用 antd `MenuProps` 与既有 `Dropdown` 组件，与「导出」Dropdown
  风格一致。
- `errMsg`/`makeTab`/`nextTabKey`/`engineLabel` 等既有工具函数完全复用，未重复造轮子。

## 测试验证结果

- 前端 `tsc --noEmit`：0 errors（修复 `<T>` → `<T,>` 后通过）
- 后端 `make check`：ruff check / format 全绿，pyrefly 0 errors，
  pytest 910 passed，覆盖率 97.82%（≥ 95% 门禁）
- 本轮纯前端改动，无后端测试变更；前端项目无测试基础设施，未新增前端测试

## 遗留事项

- LIMIT 检测正则 `\bLIMIT\s+\d+` 不识别 `LIMIT ?`（参数化占位）与
  `FETCH FIRST N ROWS ONLY`（SQL 标准替代语法），二者在 SQL 控制台场景罕见，可接受。
- 多语句 SQL（如 `SELECT 1; SELECT 2;`）`appendLimit` 只在末尾追加一次 LIMIT，
  后端通常只执行首条语句，影响有限。
- 历史记录与后端审计日志（`apps/audit`）是两套独立机制：前端历史是 per-browser 便利
  功能（仅存 SQL 文本与成功/失败），后端审计是系统级合规留痕（含用户/SQL/影响行数），
  二者不重叠。

## 下一轮计划

进入 iter-24：数据浏览高级筛选（Manager.tsx 数据表格的多列筛选、范围筛选、组合条件）。
