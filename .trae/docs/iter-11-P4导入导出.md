# 迭代记录 11 - P4 导入导出

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P4 阶段。

- [x] 19 数据浏览接口与界面：分页/排序/筛选/列显隐、行数统计
- [x] 20 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
- [x] 21 SQL 查询控制台：多 Tab、Monaco 编辑器、执行、结果表格、执行计划
- [x] 22 导入导出：CSV/Excel/SQL 脚本导入导出（流式处理大文件）
- [ ] 23 对象管理
- [ ] 24 P4 测试与文档收尾

## 迭代目标

P4-4 导入导出：交付 CSV/Excel/SQL 三种格式的流式导出 + CSV/Excel 事务批量导入，前端工具栏集成导出下拉与导入 Modal。

后端实现 10+ 个核心函数（`iter_table_rows` 流式生成器、`rows_to_csv`/`rows_to_sql` 流式编码、`export_excel` write_only 模式、`parse_csv_upload`/`parse_excel_upload` 解析器、`import_rows` 事务批量插入）；2 个接口（export 返回 StreamingHttpResponse/HttpResponse，import 接收 multipart/form-data 返回 ImportResultOut）；前端 types 新增 ExportFormat/ImportResult，api 新增 exportTable/importTable，Manager.tsx 工具栏新增导出 Dropdown 与导入 Modal（Upload.Dragger）。

## 改动文件清单

### 后端（backend/）

- `backend/apps/manager/query.py` — 新增 P4-4 段落：
  - `_format_csv_value`（None→空串、bool→1/0、datetime→ISO、bytes→hex、其余 str）
  - `_format_sql_value`（None→NULL、bool→1/0、int/float→原样、bytes→hex、datetime→ISO 字符串、str→单引号翻倍转义）
  - `_format_excel_value`（None→None、bool→bool、datetime→datetime、其余原样）
  - `iter_table_rows`（流式生成器，`fetchmany(batch_size)` 分批拉取，避免大表 OOM；SQLite 跳过 stream_results）
  - `rows_to_csv`（UTF-8 BOM + csv.DictWriter 生成器）
  - `rows_to_sql`（INSERT 语句生成器，`', '.join(values)` 格式）
  - `export_excel`（openpyxl `write_only=True` 流式写入 xlsx bytes）
  - `parse_csv_upload`（utf-8-sig 解析 BOM，DictReader 返回 (headers, rows_iter)）
  - `parse_excel_upload`（openpyxl load_workbook read_only，首行表头，返回 (headers, rows_iter)）
  - `import_rows`（事务批量插入，`engine.begin()` 包裹，列名白名单校验，batch_size=1000）
- `backend/apps/manager/schemas.py` — 新增 `ImportResultOut`（success_count/failed_count/errors）
- `backend/apps/manager/api.py` — 新增 2 个接口：
  - `POST /{ds_id}/tables/{table_name}/export`（所有登录用户可读；CSV/SQL 用 StreamingHttpResponse 流式响应；Excel 用 HttpResponse；filename*=UTF-8'' 处理中文文件名）
  - `POST /{ds_id}/tables/{table_name}/import`（designer+；`request.FILES.get("file")` 获取上传文件；扩展名白名单 .csv/.xlsx；事务失败返回 400）

### 前端（frontend/src/）

- `frontend/src/types/index.ts` — 新增 `ExportFormat`（"csv"|"xlsx"|"sql"）与 `ImportResult` 接口
- `frontend/src/api/manager.ts` — 新增 `exportTable`（responseType: "blob" 触发下载）与 `importTable`（FormData 上传）
- `frontend/src/pages/Manager.tsx` — 工具栏新增导出 Dropdown（CSV/Excel/SQL 三选项）+ 导入 Modal（Upload.Dragger，accept .csv/.xlsx，maxCount=1，beforeUpload 拦截自动上传）；新增 handleExport（Blob→URL.createObjectURL→a.click() 下载流程）与 handleImportSubmit（调用 importTable 后刷新行列表）

### 测试（tests/）

- `tests/test_manager_query.py` — 新增 20+ 单元测试：
  - `_format_csv_value`（None/bool/int/datetime/bytes/str 各类型）
  - `_format_sql_value`（None→NULL、单引号翻倍、bytes hex、datetime ISO）
  - `iter_table_rows`（流式产出、batch_size 控制）
  - `rows_to_csv`（BOM、表头、数据行、中文）
  - `rows_to_sql`（INSERT 语句、NULL、转义）
  - `export_excel`（PK 签名、openpyxl 解析回读、表头与数据行数）
  - `parse_csv_upload`（基础、BOM、空文件、中文）
  - `parse_excel_upload`（基础、空工作表）
  - `import_rows`（成功插入、空列名抛错、非法列名抛错）
- `tests/test_manager_api.py` — 新增 15+ 集成测试：
  - 导出 CSV/Excel/SQL 各格式返回 200 + Content-Disposition
  - 导出未认证返回 401、未知数据源 404、未知表 400
  - 导入 CSV/Excel 成功返回 success_count
  - 导入未认证 401、viewer 403、未知数据源 404
  - 导入主键冲突 400（事务回滚，无部分插入）
  - 导入非法文件类型 400、非法列名 400

## 关键决策与依据

1. **流式导出避免 OOM**：CSV/SQL 用 `iter_table_rows` 生成器 + `StreamingHttpResponse`，Excel 用 openpyxl `write_only=True` 模式逐行写入，避免大表全量加载到内存。
2. **事务批量导入**：`import_rows` 用 `engine.begin()` 包裹整个批量插入，任一行失败自动回滚，无部分插入；前端提示"任一行失败将全部回滚"。
3. **文件上传改用 request.FILES**：django-ninja 的 `File(None)` 参数绑定触发 ruff B008 与 pyrefly not-callable 双重检查冲突，改用 `request.FILES.get("file")` + `cast("UploadedFile | None", ...)` 绕过类型问题，更直接。
4. **导出权限放开到所有登录用户**：导出是只读操作，viewer 可用；导入是写操作，限制 designer+。
5. **CSV UTF-8 BOM**：导出 CSV 加 BOM（`\ufeff`）确保 Excel 打开中文不乱码；导入用 `utf-8-sig` 编码兼容 BOM。
6. **空工作表测试构造**：openpyxl 默认 Workbook 含一个空 sheet，pyrefly 认为 `ws.iter_rows()` 后赋值 `cell.value` 是 MergedCell 问题；改用 `wb.remove(default_ws)` + `wb.create_sheet()` 构造真正空 sheet。
7. **导出菜单集成工具栏**：导出 Dropdown 与新增行/导入按钮并列，导入按钮 designer+ 可见，导出按钮所有用户可见。
8. **前端无单测**：依赖 typecheck 保证类型正确，Ant Design + Upload.Dragger 组件 ROI 低。

## 测试验证结果

- `make check` 全套通过：
  - ruff check：All checks passed
  - ruff format --check：71 files already formatted
  - pyrefly check：0 errors (46 suppressed)
  - pytest：447 passed, 1 warning（InsecureKeyLengthWarning 为已知遗留事项，P5 处理）
  - 覆盖率：98.56%（manager/api.py 94%、manager/query.py 98%）
- `npm run typecheck`：通过

## 遗留事项

- 大数据量流式导出未做端到端浏览器手动测试（依赖 typecheck 与单元测试覆盖）
- examples/ 目录样本文件未创建（低优先级，测试已用代码构造的临时文件覆盖）
- manager/api.py 覆盖率 94% 未达 95% 目标（导出错误分支 475-476/487-490/503-506/513-517 为生成器消费阶段异常，难以在测试中触发；513-517 的 StreamingHttpResponse 流式错误分支需 mock 生成器抛错）
- P4-4 完成，下一阶段进入 P4-5 对象管理（任务 23）

## 下一轮计划

进入 P4-5 对象管理（任务 23）：视图/存储过程/函数/触发器查看与编辑。重点扫描对象管理需求，研究各数据库方言的对象元数据反射模式。
