# iter-20 P6-3 源方言化

## 需求清单

- [x] 33 源方言化：_read_source_data 按 source_db_alias 方言化，去除 sqlite 硬编码

## 迭代目标

此前源表读取（_read_source_data）的标识符引号与参数占位符均硬编码为 SQLite，
若 source_db_alias 指向 MySQL/PostgreSQL 源库，标识符引号错误（MySQL 需反引号）、
增量占位符 :name 在 MySQL/PostgreSQL 驱动下也不被支持。本轮让源读取按连接自身
方言动态构造 SQL，兼容 SQLite/MySQL/PostgreSQL。

## 改动文件清单

### 修改
- `backend/apps/sync/sync_service.py` — 新增源方言辅助（_resolve_source_dialect / _named_placeholder / _format_source_table_ref 与 _VENDOR_DIALECT_MAP），_read_source_data 三处硬编码 sqlite 改为按连接 vendor 动态方言化
- `tests/test_sync_service.py` — 补充源方言解析、跨方言标识符/表引用、增量占位符按方言选择、默认库真实读取等测试

## 关键决策与依据

### 1. 源方言取自 Django 连接自身（conn.vendor），不新增模型字段
源库方言是客观事实，由 source_db_alias 对应的 Django DATABASES[alias].ENGINE 决定，
连接对象暴露 conn.vendor（sqlite/mysql/postgresql/oracle/microsoft）。直接据此推断，
避免让用户在 SyncConfig 上重复配置方言、避免配置与实际连接不一致的风险。
vendor 取值与 EngineType（mysql/postgresql/sqlite）前三者完全一致，映射直观。

### 2. 未知 vendor 回退为 PostgreSQL 风格并告警
oracle/microsoft 等暂未接入，回退为标准 SQL 双引号标识符（PostgreSQL 风格）并记录
warning，使源读取不因方言未知直接崩溃，同时为后续方言接入留出可观测的信号。

### 3. 增量占位符按方言切换（named vs pyformat）
Django DB-API 游标 paramstyle 因后端而异：sqlite3 支持 named（:name）但不支持 pyformat；
MySQLdb/psycopg 用 pyformat（%(name)s）。经实测确认 sqlite3 对 %(name)s 报语法错误。
故统一 dict 传参、占位符按方言构造（_named_placeholder），保证跨方言可用。

### 4. 源表引用与目标表对齐，支持 source_schema
新增 _format_source_table_ref，非 SQLite 时输出 schema.table（方言化引号），
与既有 _format_target_table_ref 逻辑一致，复用 _quote_ident 的方言化引号。

## 整合优化情况

- 源读取的标识符引用统一走既有 _quote_ident，未新增重复的引号逻辑。
- _format_source_table_ref 与 _format_target_table_ref 保持相同的「非 sqlite 才拼 schema」
  判定，两处语义对齐，便于后续统一维护。

## 测试验证结果

- ruff check / format：全绿
- pyrefly：0 errors
- pytest：847 passed，覆盖率 98.08%（≥ 上一轮）
- 新增覆盖：源方言解析（含未知回退）、跨方言标识符/表引用、增量占位符按方言选择、
  默认库（sqlite vendor）真实全量读取

## 遗留事项

- 源库为 MySQL/PostgreSQL 的真实读取仅经单元级方言构造验证，端到端真实连接测试待有
  对应测试库环境后补充（当前 CI 仅 SQLite）。
- 增量 SQL 仅按 timestamp_field > last_sync 过滤，跨方言的时间类型/时区差异待后续按需处理。
- 同步监控与告警（req 31）、P6 测试与文档补全（req 34）待后续迭代。

## 下一轮计划

P6 剩余候选：31 同步监控与告警（成功率/平均耗时统计接口 + 失败告警 + 前端监控面板），
或 34 P6 测试与文档补全。下一轮开始前与用户确认优先级。
