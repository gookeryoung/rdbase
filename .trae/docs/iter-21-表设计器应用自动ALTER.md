# iter-21 表设计器应用自动 ALTER

## 需求清单

- [x] 修复：数据库设计修改字段后应用失败（表已存在仍走 CREATE 导致 "table already exists"）

## 迭代目标

表设计器「应用」与「DDL 预览」在前端始终不传 `old_spec`，后端因此在 `old_spec=None` 时
固定走 `generate_create_table`。首次应用（CREATE）成功后草稿置为 applied，用户修改字段
再次应用时仍生成 CREATE TABLE，目标表已存在 → 400 失败。本轮让后端在未传 `old_spec` 时
自动判断：目标表不存在走 CREATE，已存在则反射当前表结构作为 `old_spec` 走 ALTER，前端无需改动。

## 改动文件清单

### 修改
- `backend/apps/designer/api.py` — 新增反射转 spec 辅助组（`_REFLECTED_TYPE_RE` / `_parse_reflected_type` /
  `_reflect_table_to_spec` / `_resolve_old_spec`），`preview_ddl_view` 与 `apply_draft_view` 在
  `old_spec is None` 时调用 `_resolve_old_spec` 自动反射判断 CREATE/ALTER
- `tests/test_designer_drafts_api.py` — 新增 5 个测试覆盖自动判断（表已存在无变更 executed=0、
  新增字段 ADD COLUMN、表不存在走 CREATE、预览自动 ALTER、预览无变更空语句）；
  更新 `test_apply_draft_execution_failure_returns_400` 用「删除不存在索引」触发执行阶段失败
- `tests/test_audit_integration.py` — 更新 `test_designer_apply_ddl_failure_logs_audit` 用
  「old_spec 含不存在索引 → DROP INDEX 执行失败」替代原「CREATE 重名失败」场景，确保仍走执行失败审计分支

## 关键决策与依据

### 1. 后端自动反射判断，前端不改
前端无法准确获知目标库当前表结构（只有后端能反射），让前端传 `old_spec` 不可靠。
后端用 `sa_inspect(engine).has_table` 判断表是否存在：不存在返回 None（CREATE），
存在则 `inspect_table` 反射后转 `TableDesignSpec`（ALTER）。`apply_draft_view` 与
`preview_ddl_view` 统一接入，预览与应用行为一致。

### 2. 反射转 spec 时规整「语义等价但表示不同」的差异
反射得到的 `ColumnMeta` 与设计器 `FieldSpec` 存在表示差异，直接比较会误判触发无意义 ALTER：
- 主键列强制 `nullable=False`（部分方言反射主键列返回 nullable=True，但主键必非空）。
- SQLite 的 `INTEGER PRIMARY KEY` 隐式 ROWID 自增，对齐 `autoincrement=True`（SQLAlchemy 反射返回 False）。
- 自增主键 `default` 规整为 None（序列由数据库隐含管理，反射得到的序列表达式与设计器 None 不一致）。
- 反射类型字符串解析为基础类型 + 长度（`VARCHAR(50)` → `VARCHAR` + 50），与 `FieldSpec.type/length` 对齐。
- 外键 `on_delete` 反射无法获取，统一填 RESTRICT（外键差异仅按 name 比较，不影响）。

### 3. SQLite 修改字段定义的固有限制保留清晰提示
SQLite 不支持修改列类型/可空性/默认值（`_format_alter_column` 抛 `DDLOperationNotSupported`）。
本轮修复后：新增字段、删除字段、表重命名在 SQLite 下可正常 ALTER；修改字段类型仍会返回 400
并提示「SQLite 不支持修改字段定义，请使用 DROP COLUMN + ADD COLUMN 替代」，这是 SQLite 本身限制。
错误信息比原先「table already exists」准确，用户能理解根因。

## 整合优化情况

- 复用既有 `inspect_table` 反射与 `generate_ddl` 入口，未新增 DDL 生成路径。
- 反射转 spec 的类型解析正则统一处理含长度/精度的类型字符串，兼容 `DOUBLE PRECISION` 等含空格类型名。
- `apply_draft_view` 与 `preview_ddl_view` 共用 `_resolve_old_spec`，逻辑一致。

## 测试验证结果

- ruff check / format：全绿
- pyrefly：0 errors
- pytest：879 passed，覆盖率 98.01%（≥ 上一轮 98.08% 同档，≥ 95% 门禁）
- 新增覆盖：未传 old_spec 时表已存在自动 ALTER（无变更 executed=0、新增字段 ADD COLUMN）、
  表不存在走 CREATE、DDL 预览自动 ALTER、DDL 预览无变更空语句

## 遗留事项

- PostgreSQL SERIAL 主键反射类型为 INTEGER，与设计器 SERIAL 表示不一致，可能误判 ALTER TYPE；
  待 PostgreSQL 真实环境端到端验证后按需规整。
- 外键 `on_delete` 修改不会被检测（反射无此信息且外键差异仅按 name 比较），属既有局限。
- SQLite 修改字段类型/可空性/默认值仍不支持（方言固有限制，错误提示已清晰）。

## 下一轮计划

修复已交付。后续可推进 req 31（同步监控与告警）或 req 34（P6 测试与文档补全），
下一轮开始前与用户确认优先级。
