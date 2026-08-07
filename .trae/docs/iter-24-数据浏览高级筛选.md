# iter-24 数据浏览高级筛选

## 需求清单

- [x] 数据浏览页（Manager.tsx）新增高级筛选功能，支持多列 AND 组合、多种操作符
- [x] 筛选条件持久化到 localStorage，按数据源 + 表名分键
- [x] 与既有列头模糊筛选共存（同列冲突时高级筛选优先）

## 迭代目标

在 Manager.tsx 数据表格工具栏新增"高级筛选"入口（Drawer 形式），允许用户为多列分别指定操作符（等于/不等于/大于/大于等于/小于/小于等于/包含 LIKE/包含于 IN）与值，条件以 AND 连接，提交后立即触发查询。条件按 `rdbase:advFilters:{dsId}:{tableName}` 持久化到 localStorage，切换数据源/表时自动加载对应历史条件，关闭浏览器后再次打开仍可恢复。

## 改动文件清单

- `frontend/src/pages/Manager.tsx`：新增高级筛选 Drawer、状态、持久化、filters 合并逻辑、工具栏按钮（Badge 显示生效条件数）

后端无改动：既有 `query_table_rows` 已支持完整操作符 `eq/ne/gt/lt/ge/le/like/in`（见 [query.py](file:///f:/Dev/rdbase/backend/apps/manager/query.py) 的 `_COMPARATORS` 与 `_build_where_clause`），本次只在前端启用全部操作符。

## 关键决策与依据

1. **保留列头模糊筛选入口**：原列头 Input 作为"快速模糊匹配"依然有用，不删除以免破坏用户习惯；新增的高级筛选作为"精细筛选"补充。
2. **同列冲突时高级筛选优先**：后端 `filters` 是按列名 dict（每列只能一个条件），同列同时存在列头 like 与高级筛选时，让高级筛选覆盖列头 like（在 `loadRows` 中先放列头、再放高级筛选）。这是最简方案，避免同步两套 UI 状态。
3. **持久化分键粒度**：`rdbase:advFilters:{dsId}:{tableName}`，与 SqlConsole 的 `rdbase:sqlTabs:{dsId}` 模式一致但更细粒度（加表名），避免不同表条件互相污染。
4. **值类型自动推断**：纯数字字符串自动转 `Number`（避免后端按字符串比较数值列时排序/比较语义错误）；IN 操作符逗号分隔，全数字时转 `number[]`，否则保留字符串数组。
5. **空值条件跳过**：`buildAdvancedFilters` 中 value 为空（或 IN 全为空）的条件不生成 filter，允许用户在 Drawer 中保留半成品条件而不触发后端报错。
6. **Drawer 而非 Modal**：多条件编辑场景下 Drawer 右侧抽屉更合适，不遮挡表格，可边编辑边观察结果。

## 代码实现情况

### 新增类型与常量

- `AdvancedFilter` 接口：`{ column: string; op: RowFilterOp; value: string }`
- `ADV_FILTER_OPS`：8 个操作符选项（与后端 `_COMPARATORS` 对齐）
- `advFiltersStorageKey(dsId, tableName)`：localStorage 键名生成器
- `safeReadAdv`/`safeWriteAdv`：localStorage 安全读写（解析失败/配额满静默降级）
- `buildAdvancedFilters(advFilters)`：将 UI 状态转为后端 `Record<string, RowFilter>`，处理 like 包裹 %、in 逗号分隔、数字自动转换、空值跳过

### 新增状态

- `advFilters: AdvancedFilter[]`：高级筛选条件列表
- `advFilterOpen: boolean`：Drawer 开关
- `activeAdvFilterCount`（useMemo）：当前生效条件数，用于工具栏 Badge

### 新增处理函数

- `handleAdvFilterAdd`：追加一条空条件（默认列取 `columns[0]`、操作符 `eq`）
- `handleAdvFilterChange(idx, patch)`：修改某条条件的 column/op/value
- `handleAdvFilterRemove(idx)`：删除某条条件
- `handleAdvFilterClear`：清空全部条件

### loadRows 合并逻辑

```ts
const filters: Record<string, RowFilter> = {};
// 1. 列头模糊筛选（基础入口）
Object.entries(filterInputs).forEach(([col, kw]) => {
  if (kw.trim()) filters[col] = { op: "like", val: `%${kw.trim()}%` };
});
// 2. 高级筛选覆盖同列
Object.entries(buildAdvancedFilters(advFilters)).forEach(([col, cond]) => {
  filters[col] = cond;
});
```

### 持久化 useEffect

- 切换数据源/表时从 localStorage 加载对应条件（无则置空）
- `advFilters` 变化时写回 localStorage（仅在已选定表时写入）

### UI

- 工具栏新增"高级筛选"按钮（`FilterOutlined` + `Badge` 显示生效条件数），位于"列显隐"与"刷新"之间
- Drawer 内：操作说明 Text + 条件列表（每行：列 Select + 操作符 Select + 值 Input + 删除按钮）+ 底部"应用筛选"按钮
- Drawer 头部 extra 区：清空 + 添加条件

## 整合优化情况

- 复用既有 `RowFilter`/`RowFilterOp` 类型（[types/index.ts](file:///f:/Dev/rdbase/frontend/src/types/index.ts)），无需扩展类型
- 复用后端已支持的完整操作符，无需改后端
- localStorage 安全读写模式参考 SqlConsole.tsx 的 `safeRead`/`safeWrite`

## 测试验证结果

- 前端 typecheck：`cd frontend; npx tsc --noEmit` 通过（exit 0）
- 后端门禁：`make check` 通过（910 passed, coverage 97.82% ≥ 95%）
- 无前端测试基础设施，本次未新增前端单测（与既有约定一致）

## 遗留事项

- 高级筛选未支持 OR 组合（后端 `filters` 是 dict 结构，每列一个条件，AND 连接；OR 需后端结构调整，超出本次范围）
- 未支持 IS NULL / IS NOT NULL（后端 `_COMPARATORS` 未包含，需后端扩展）
- 同列冲突时高级筛选静默覆盖列头筛选，无 UI 提示；若用户在列头输入关键词后又在该列加高级筛选，列头输入框仍显示原值但实际不生效（可后续加视觉提示，本次不做以避免复杂化）

## 下一轮计划

iter-25：表结构与长字段查看（待启动）
