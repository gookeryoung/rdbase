# iter-16 同步模块前端增强

## 需求清单

- [x] 前端类型定义更新：添加调度字段和新接口类型（SyncScheduleUpdate/SyncPreview/SyncBatchRequest/SyncBatchResult）
- [x] 创建 sync API 模块（frontend/src/api/sync.ts）
- [x] 重写 Sync.tsx 使用 antd 组件库替代 MUI
- [x] 添加调度设置面板（cron 表达式、启用开关、最大重试次数）
- [x] 添加预览功能（采样数据展示、字段列表、错误提示）
- [x] 添加批量触发功能（多选、批量执行、结果统计）
- [x] TypeScript 类型检查通过
- [x] 后端测试 61 个全部通过

## 迭代目标

将后端已实现的同步增强功能（预览、批量触发、调度设置）同步到前端，同时修复 Sync.tsx 使用错误的 UI 库（MUI → antd）和错误的 API 客户端（不存在的 apiFetch → axios client）问题。

## 改动文件清单

### 前端新增
- `frontend/src/api/sync.ts` — 同步模块 API 客户端，封装所有同步相关接口调用

### 前端修改
- `frontend/src/types/index.ts` — SyncConfig 添加调度字段；新增 SyncScheduleUpdate/SyncPreview/SyncBatchRequest/SyncBatchResult 类型
- `frontend/src/pages/Sync.tsx` — 完全重写：MUI → antd；apiFetch → client；新增调度/预览/批量功能

## 关键决策与依据

### 1. 创建独立 sync API 模块
参照其他模块（datasources/settings/audit）的 API 层模式，创建 `frontend/src/api/sync.ts`，使用项目统一的 axios client，避免直接在组件中拼 URL。

### 2. 完全重写 Sync.tsx
原 Sync.tsx 使用了 MUI 组件库和不存在的 `../lib/api` 模块，与项目实际技术栈（antd + axios）不匹配。完全重写而非局部修补，确保代码一致性和可维护性。

### 3. 批量选择使用 antd Table 内置 rowSelection
而非手动添加 checkbox 列，避免重复选择逻辑。

### 4. 预览数据动态列
采样数据的列从 `sample_rows[0]` 的 keys 动态生成，适应不同表结构。

## 测试验证结果

- TypeScript 类型检查：通过（`tsc --noEmit` 无错误）
- 后端测试：61 passed, 0 failed
- Ruff lint：通过

## 遗留事项

- 前端页面尚未与后端进行实际联调（需启动开发服务器）
- 原有 Sync.tsx 的 MUI 依赖可从 package.json 清理（当前未安装 MUI）

## 下一轮计划

- P5-3 Docker 化：后端/前端 Dockerfile、nginx 配置、docker-compose
- 或前端联调验证
