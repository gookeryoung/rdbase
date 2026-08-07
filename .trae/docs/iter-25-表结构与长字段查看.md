# iter-25 表结构与长字段查看

## 需求清单

- [x] 数据浏览页（Manager.tsx）新增"表结构"Drawer，展示字段/索引/外键/唯一约束完整元数据
- [x] 数据表格中超长字段值（>200 字符或 JSON 对象）可点击查看完整内容

## 迭代目标

在 Manager.tsx 工具栏新增"表结构"入口（Drawer 形式），展示当前表的完整元数据（字段列表含 PK/类型/可空/默认/属性/注释、索引、外键、唯一约束），数据来自既有 `retrieveTable` API（之前仅取 `primary_key`，现在复用整个 `TableDetail`）。同时为数据表格中超长字段值提供点击查看入口，避免 `ellipsis` 截断后无法查看完整内容；长字段在 Modal 中以 Monaco 编辑器只读展示，JSON 对象自动识别并以 `json` 语法高亮。

## 改动文件清单

- `frontend/src/pages/Manager.tsx`：新增表结构 Drawer、长字段查看 Modal、`tableDetail` 状态、表格 cell render 长字段截断与点击逻辑

后端无改动：既有 `retrieveTable` API（[designer/api.py](file:///f:/Dev/rdbase/backend/apps/designer/api.py) 的 `retrieve_table_view`）已返回完整 `TableDetail`（含 `columns`/`indexes`/`foreign_keys`/`unique_constraints`），前端之前仅取 `primary_key`，本次复用整个响应。

## 关键决策与依据

1. **复用 `retrieveTable` 而非新增 API**：切表时已调用 `retrieveTable` 拉取主键信息，本次扩展为存储完整 `TableDetail`，无需新增请求。后端 `inspect_table`（[inspector.py](file:///f:/Dev/rdbase/backend/apps/designer/inspector.py)）已一次性返回字段/主键/外键/索引/唯一约束/注释，无需改后端。
2. **长字段阈值 200 字符**：超过 200 字符的字符串或任何 JSON 对象在 cell 中截断显示并加 `...` 后缀，点击打开 Modal 查看完整内容。阈值取 200 是经验值，兼顾表格可读性与大多数值不触发截断的性能。
3. **Monaco 编辑器只读展示长字段**：复用 SqlConsole/对象定义已有的 Monaco 编辑器，支持 JSON 语法高亮、自动换行、滚动，体验优于 `<pre>` 或 `Input.TextArea`。JSON 对象用 `defaultLanguage="json"`，纯文本用 `plaintext`。
4. **表结构 Drawer 而非 Modal**：表结构可能较长（字段多、索引多），Drawer 右侧抽屉不遮挡表格，可边看结构边对照数据；内部用 antd `Table` 组件展示字段/索引/外键，支持横向滚动。
5. **`Table<T>` 显式泛型**：antd `Table` 的 `columns` render 参数 `record` 类型由 `dataSource` 推断，但多列 render 函数的 `record` 类型合并后可能丢失字段（TS 推断 union）。给 `Table<ColumnMeta>` 等显式泛型可让 render 的 `r` 自动推断为完整类型，无需手动标注。
6. **切表时清空 `tableDetail`**：避免切换到新表时 Drawer 闪烁显示旧表结构，由 `retrieveTable` 异步加载后填充。

## 代码实现情况

### 新增状态

- `tableDetail: TableDetail | null`：完整表元数据（切表时由 `retrieveTable` 加载）
- `structureOpen: boolean`：表结构 Drawer 开关
- `longValueModal: { column: string; value: string; isJson: boolean } | null`：长字段查看 Modal 状态

### retrieveTable 回调扩展

切表时的 `retrieveTable` 回调从仅 `setPkColumns` 扩展为同时 `setTableDetail`，失败时两者都清空。切表前先 `setTableDetail(null)` 避免 Drawer 显示旧数据。

### 表格 cell render 长字段处理

```tsx
render: (val: unknown) => {
  if (val === null || val === undefined) return <Text type="secondary">NULL</Text>;
  if (typeof val === "boolean") return String(val);
  if (typeof val === "object") {
    const json = JSON.stringify(val);
    const truncated = json.length > 200 ? `${json.slice(0, 200)}...` : json;
    return <Button type="link" size="small" onClick={() => setLongValueModal({ column: col, value: json, isJson: true })}>{truncated}</Button>;
  }
  const str = String(val);
  if (str.length > 200) {
    return <Button type="link" size="small" onClick={() => setLongValueModal({ column: col, value: str, isJson: false })}>{`${str.slice(0, 200)}...`}</Button>;
  }
  return str;
}
```

### 工具栏新增"表结构"按钮

位于"高级筛选"与"刷新"之间，`TableOutlined` 图标。

### 表结构 Drawer

- 表注释（若有）
- 字段表（`Table<ColumnMeta>`）：字段名（PK 标 Tag）、类型、可空、默认、属性（AUTO/UNIQUE Tag + 注释）
- 索引表（`Table<IndexMeta>`，仅当有索引时）：名称、唯一、列
- 外键表（`Table<ForeignKeyMeta>`，仅当有外键时）：名称、本表列、引用表、引用列
- 唯一约束列表（仅当有多列唯一约束时）：UNIQUE Tag + 列名

### 长字段查看 Modal

- 标题：`字段内容：<列名>`
- 顶部：长度 + JSON Tag（若是对象）
- Monaco 编辑器（只读、自动换行、minimap 关闭）：JSON 用 `json` 语法，其他用 `plaintext`

## 整合优化情况

- 复用既有 `retrieveTable` API 与 `TableDetail`/`ColumnMeta`/`IndexMeta`/`ForeignKeyMeta` 类型，无新增 API
- 复用 Monaco 编辑器（SqlConsole/对象定义已用），无新增依赖
- `Table<T>` 显式泛型避免 TS 推断 union 丢失字段类型

## 测试验证结果

- 前端 typecheck：`cd frontend; npx tsc --noEmit` 通过（exit 0）
- 后端门禁：`make check` 通过（910 passed, coverage 97.82% ≥ 95%）
- 无前端测试基础设施，本次未新增前端单测（与既有约定一致）

## 遗留事项

- 表结构 Drawer 未展示列的 `comment` 完整内容（已在"属性"列内联显示，超长注释可能截断，但 Drawer 内有横向滚动，可接受）
- 长字段查看 Modal 未支持复制按钮（Monaco 编辑器右键菜单自带复制，已够用）
- 未支持二进制字段（如 BLOB）的查看，后端返回的二进制数据当前会被 `String(val)` 转为乱码；二进制字段查看需后端返回 base64 或 hex 编码，超出本次范围

## 下一轮计划

待用户指定下一迭代方向。
