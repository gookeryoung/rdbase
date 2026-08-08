# iter-35 前端数据爬取管理界面

## 需求清单

- [x] 35 定时调度 + 日志告警 + 前端管理界面 + 文档：前端任务管理页（任务列表/创建/执行/日志/告警/统计），路由与菜单接入，类型与 API 层补全

> 后端定时调度（run_scheduled_ingest）、IngestLog/IngestAlert、监控接口、CRUD/执行/日志/告警/统计 API 在 iter-31 已实现并通过测试，本轮聚焦前端 UI 闭环。

## 迭代目标

为 ingest 模块补齐前端管理界面，使管理员可在浏览器完成爬取任务的全生命周期管理：浏览任务列表、按源类型动态表单创建/编辑任务（含解析配置、请求配置、加密请求头、字段映射、调度）、手动触发执行并查看子进程 returncode/stderr、查询任务日志、监控统计与告警确认。完成后 ingest 模块前后端贯通，用户可在 UI 上完整使用 iter-31~34 实现的四类源爬取能力。

## 改动文件清单

新增：
- [frontend/src/api/ingest.ts](file:///home/zhou/rdbase/frontend/src/api/ingest.ts)：ingest API 层，10 个函数（listIngestTasks/createIngestTask/retrieveIngestTask/updateIngestTask/deleteIngestTask/runIngestTask/listIngestTaskLogs/listIngestAlerts/ackIngestAlert/getIngestStats）。注意后端 list 接口返回数组（非 `{items}` 包装），与 sync 模块不同。
- [frontend/src/pages/Ingest.tsx](file:///home/zhou/rdbase/frontend/src/pages/Ingest.tsx)：爬取管理主页，~1070 行。任务列表 + 创建/编辑 Modal + 执行结果 Modal + 日志 Modal + 监控面板 Modal。

修改：
- [frontend/src/types/index.ts](file:///home/zhou/rdbase/frontend/src/types/index.ts)：追加 P7 数据爬取类型块（IngestSourceType/IngestStatus/IngestLogStatus/IngestConflictStrategy/IngestAuthType/IngestFieldMapping/IngestTask/IngestTaskCreate/IngestTaskUpdate/IngestLog/IngestAlert/IngestRunResult/IngestStats），复用既有 FieldMappingType 与 AlertLevel。
- [frontend/src/routes/index.tsx](file:///home/zhou/rdbase/frontend/src/routes/index.tsx)：新增 `/ingest` 路由，admin only，仿 sync 模式。
- [frontend/src/layouts/MainLayout.tsx](file:///home/zhou/rdbase/frontend/src/layouts/MainLayout.tsx)：侧边栏新增「数据爬取」菜单项（CloudDownloadOutlined 图标，admin only），位于「数据同步」之后。

## 关键决策与依据

1. **API 层签名与 sync 模块的差异**：sync 的 list 接口返回 `{items, total}` 包装，ingest 的 list 接口（list_tasks/list_alerts/list_task_logs）直接返回数组。前端 ingest API 层据此返回 `Promise<IngestTask[]>` 而非 `Promise<{items: IngestTask[]}>`，避免无谓包装。`listIngestAlerts(all=false)` 直接对应后端 `?all=true` 查询参数。
2. **parse_config 按源类型动态渲染**：iter-31~34 实现的 4 类 spider 各有不同 parse_config 字段（API: items_path/next_page_path/next_page_max；HTML: selector_type/container_selector/fields/next_page_selector/next_page_attr/next_page_max；FILE: format/encoding/delimiter/sheet/items_path；RSS: include_feed_metadata）。创建/编辑表单按 source_type 切换渲染对应字段，避免用户面对不相关配置。切换源类型时重置 parse_config 为该类型的默认值，防止残留字段污染。
3. **HTML fields 配置用 JSON 文本域**：HTML spider 的 `fields` 是嵌套字典（字段名 → `{selector, attr}` 或 selector 字符串），结构灵活但难以用简单表单表达。采用 JSON TextArea 编辑，保存前 `JSON.parse` 校验并显示错误信息，校验通过才合并到 parse_config。其余源类型的标量字段用 Input/InputNumber/Select 渲染，用户体验更友好。
4. **请求头加密策略的前端呈现**：后端 API 仅返回 `has_headers` 标志，不回显明文。前端在编辑已有任务时显示提示「此任务已配置请求头（不回显）。如需保留原值请勿添加新项；添加新项将整体覆盖」。新建任务时无此提示。保存时若用户未添加新 header 项，编辑模式下不发送 `headers` 字段（后端保留原值）；新建模式或用户添加了新项则发送 `headers` 字段整体覆盖。
5. **执行结果用独立 Modal 展示**：sync 的 triggerSync 直接返回 SyncResult（含状态与统计），而 ingest 的 run 接口返回 `IngestRunOut`（含子进程 returncode + log + stderr）。爬取是子进程运行 Scrapy，可能耗时较长，前端用 Spin 占位，结果在独立 Modal 展示 returncode/log 统计/stderr 输出，便于排查子进程级失败（如 Scrapy 启动异常、依赖缺失等 returncode≠0 但 log 可能未生成的场景）。
6. **监控面板的告警过滤**：sync 模块的 listSyncAlerts 支持 `acknowledged` 参数，ingest 后端只支持 `?all=true|false`（默认仅未确认）。前端 `onlyUnacked` 复选框映射到 `listIngestAlerts(!onlyUnacked)`，语义清晰。未确认告警数量同时用于顶部 Badge 徽标，进入页面即加载。
7. **路由与菜单仅 admin 可见**：与 sync 模块一致，ingest 的所有写操作（create/update/delete/run/ack）后端都要求 admin，读操作所有登录用户可访问。前端路由用 RoleRoute admin 守卫，菜单项标记 `roles: [Role.ADMIN]`，普通用户不可见。这与后端权限模型对齐，避免 viewer/designer 进入页面后遭遇 403。
8. **类型设计：IngestTaskCreate.parse_config/request_config 标记为必填**：后端 schema 这两个字段有默认值 `{}`，但前端 `createEmptyTask()` 与 `handleOpenEdit()` 总是显式设置它们。标记为必填消除 `editingTask.parse_config` 可能为 undefined 的类型告警，符合实际使用不变式。
9. **复用 Sync.tsx 的交互模式**：任务列表、字段映射内嵌表格、监控面板的统计卡片+告警表格、日志 Modal 的刷新按钮等交互模式与 sync 一致，降低用户学习成本。未实现 sync 的批量触发/预览功能，因为 ingest 后端未提供对应端点（爬取是子进程同步执行，批量触发会阻塞 web 进程；预览需 spider dry-run 未实现）。

## 代码实现情况

### API 层（api/ingest.ts）
- 10 个函数，命名与 sync 模块对齐（listXxx/createXxx/retrieveXxx/updateXxx/deleteXxx/runXxx/listXxxLogs/listXxxAlerts/ackXxxAlert/getXxxStats）。
- list 类函数直接返回数组（后端 `JsonResponse(body, safe=False)`）。
- `listIngestAlerts(all=false)` 通过 `?all=true` 查询参数控制。
- `getIngestStats(days?)` 通过 `?days=N` 查询参数限定时间范围。

### 类型层（types/index.ts）
- 13 个新类型/接口，覆盖 ingest 全部数据结构与 API 请求/响应。
- `IngestFieldMapping` 复用 sync 的 `FieldMappingType`（"direct" | "constant"），与后端枚举值一致。
- `IngestAlert.level` 复用 sync 的 `AlertLevel`（"warning" | "error"）。
- `IngestTaskUpdate extends Partial<IngestTaskCreate>` 并新增 `status?` 字段，对应后端全量更新语义。

### 页面层（pages/Ingest.tsx）
- 状态管理：useState + useCallback + useEffect，无 Zustand（与 sync 一致，页面级状态无需全局共享）。
- 创建/编辑表单：
  - 基本信息区（名称/描述/源类型/源 URL/鉴权类型）
  - 解析配置区（按 source_type 动态渲染，HTML 的 fields 用 JSON 文本域）
  - 请求配置区（并发/超时/下载延迟/UA/Cookies/robots）
  - 请求头区（key-value 表格编辑，Input.Password 脱敏，编辑时提示原值不回显）
  - 目标写入区（目标数据源/目标表/冲突策略/批大小/调度开关/Cron）
  - 字段映射区（内嵌表格，支持 direct/constant 两种映射类型，可标记主键）
- 执行结果 Modal：returncode 统计卡 + log 状态/行数统计卡 + 错误信息 Alert + stderr 滚动文本块。
- 日志 Modal：刷新按钮 + 表格分页展示。
- 监控面板 Modal：统计范围 Select + 仅看未确认 Checkbox + 8 个统计卡（成功率/执行次数/失败/平均耗时/成功/部分/累计写入/累计跳过）+ 告警表格。

### 路由与菜单
- `/ingest` 路由：`RoleRoute allowedRoles={[Role.ADMIN]}` 包裹，children index 指向 `<Ingest />`，位于 sync 之后。
- 菜单项：「数据爬取」CloudDownloadOutlined 图标，admin only，位于「数据同步」之后。

## 整合优化情况

- 类型复用：IngestFieldMapping.mapping_type 与 SyncFieldMapping.mapping_type 共用 `FieldMappingType`；IngestAlert.level 与 SyncAlert.level 共用 `AlertLevel`。避免重复定义相同语义类型。
- API 层模式复用：api/ingest.ts 的命名与结构与 api/sync.ts 对齐，便于维护者交叉参考。
- 页面交互模式复用：Ingest.tsx 的 Modal 布局、Table 列定义风格、监控面板统计卡布局与 Sync.tsx 一致，用户从同步页迁移到爬取页无学习成本。
- 字段映射编辑器：完全复用 sync 的内嵌表格交互（添加/删除行、direct/constant 切换、主键勾选），代码结构一致。

## 测试验证结果

- 前端 `tsc --noEmit`：通过（0 errors）。
- 前端 eslint：项目未安装 eslint（package.json 有 lint 脚本但 node_modules 无 eslint 依赖），跳过。
- 前端无单元测试套件（项目未配置 vitest/jest）。
- 后端未改动，iter-31 已有的 44 项 ingest 测试与 iter-32~34 的 spider 测试不受影响。
- IDE 诊断（GetDiagnostics）：0 issues。

## 遗留事项

- 前端 eslint 未配置：package.json 声明了 `lint` 脚本但未安装 eslint 依赖，全项目无 eslint 配置文件。这是项目级既有问题，本轮不引入也不修复。
- 前端无单元测试：项目未配置前端测试框架（vitest/jest），Ingest.tsx 的交互逻辑无自动化测试覆盖。建议后续单独迭代引入 vitest + @testing-library/react。
- 执行爬取是同步阻塞：后端 `POST /ingest/tasks/{id}/run` 同步等待子进程返回，大型爬取任务可能耗时数十秒至分钟级，前端 Spin 占位但 HTTP 请求可能超时（client.ts 默认 timeout 30s）。iter-31 已标注「后续 iter-35 可改为异步」，本轮未改后端（聚焦前端），如遇超时需调大 client timeout 或后端改异步。建议后续迭代将 run 改为异步任务队列。
- HTML fields 配置用 JSON 文本域：对非技术用户不够友好。可考虑后续迭代提供「字段名 + 选择器 + 属性」三列表格的可视化编辑器，但当前 JSON 编辑器对高级用户更灵活且实现成本低。
- ingest 模块 API 端到端测试（HTML/FILE/RSS 源类型的完整 HTTP 流程）仍未补充（iter-34 遗留），当前测试聚焦 spider parse 逻辑与模型/引擎/命令/API 单元测试。

## 下一轮计划

iter-35 完成 ingest 模块前端管理界面，至此 req-02「外部数据爬取能力」P7 里程碑的全部子项（31~35）已完成。req-02 验收标准第 6 条「前端管理界面可用」达成。

后续候选方向（按优先级）：
1. 将 ingest run 改为异步任务（Celery/RQ 或后台线程），避免 web 进程阻塞与 HTTP 超时。
2. 前端引入 vitest + @testing-library/react，覆盖 Sync/Ingest 等管理页面的交互逻辑。
3. HTML fields 配置可视化编辑器（字段名/选择器/属性三列表格）。
4. ingest 模块 API 端到端测试补全（iter-34 遗留）。
5. README/用户手册更新 ingest 模块使用说明（req-02 验收标准第 6 条「README/手册同步更新」）。
