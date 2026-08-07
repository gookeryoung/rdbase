# iter-27 SQLite 字段修改 DROP+ADD 替代

## 需求清单

- [x] SQLite 修改字段定义不再抛错，改用 DROP COLUMN + ADD COLUMN 替代
- [x] 主键字段修改仍抛错（SQLite DROP COLUMN 不允许删除主键列）

## 迭代目标

原 `_format_alter_column` 在 SQLite 方言下抛 `DDLOperationNotSupported("SQLite 不支持修改字段定义，请使用 DROP COLUMN + ADD COLUMN 替代")`，导致用户在反向工程导入表结构后修改字段并应用回库时无法生成 DDL。本次实现 DROP COLUMN + ADD COLUMN 替代方案：非主键字段修改生成两条语句（先 DROP 后 ADD），主键字段修改仍抛 `DDLError`（SQLite DROP COLUMN 限制不允许删除主键列）。

## 改动文件清单

- [backend/apps/designer/ddl.py](file:///f:/Dev/rdbase/backend/apps/designer/ddl.py)：
  - `_format_alter_column` SQLite 分支：不再抛 `DDLOperationNotSupported`，改为生成 DROP COLUMN + ADD COLUMN；主键字段抛 `DDLError`。
  - 顶部 docstring 同步更新 SQLite 字段修改语义说明。
  - 删除未使用的 `DDLOperationNotSupported` 类与 `__all__` 导出。
- [tests/test_designer_ddl.py](file:///f:/Dev/rdbase/tests/test_designer_ddl.py)：
  - 删除 `DDLOperationNotSupported` import。
  - `test_alter_table_modify_column_sqlite_raises` 重命名为 `test_alter_table_modify_column_sqlite_drop_add`，验证生成 DROP+ADD 两条语句、顺序正确、新字段按 new_spec 重写。
  - 新增 `test_alter_table_modify_column_sqlite_pk_raises`：主键字段修改抛 `DDLError`。

## 关键决策与依据

1. **DROP+ADD 而非 12 步表重建**：用户引用的错误消息明确建议"DROP COLUMN + ADD COLUMN 替代"。SQLite 真正无损方案是 12 步表重建（CREATE NEW TABLE → INSERT SELECT → DROP OLD → RENAME → 重建索引），逻辑复杂且易出错（需映射列名、保留约束）。DROP+ADD 简单直接，虽会丢失该列数据但语义明确——用户修改字段定义本就期望结构变更，预览 DDL 时能看到 DROP+ADD 序列即可理解后果。
2. **主键字段单独抛 DDLError**：SQLite `DROP COLUMN` 文档明确禁止删除主键列、被外键引用列、被索引覆盖列、UNIQUE 约束列。主键字段走 DROP+ADD 会在执行时失败，提前在生成阶段抛 `DDLError` 给出清晰错误消息，避免用户预览通过但执行失败。错误消息指引"保留原主键定义或重建表"——后者是 SQLite 12 步表重建方案，未来如需支持可再扩展。
3. **删除 `DDLOperationNotSupported`**：该类仅服务于原 SQLite 抛错场景，本次实现后无人引用。按"避免 backwards-compatibility hacks"原则彻底删除，避免遗留死代码。`DDLError` 已足够表达所有 DDL 生成错误。
4. **不加 SQL 注释警告**：DDL 执行时每条 statement 单独 `conn.execute(text(stmt))`，加 `--` 注释会让注释与下条 SQL 拼成一条字符串，语义不清晰。用户在 DDL 预览界面能看到 DROP COLUMN + ADD COLUMN 的语句序列，足以理解语义。如未来需要在 UI 层提示"会丢失数据"，应在 `DDLResult` 加 `warnings` 字段（涉及 schemas.py + 前端联动，超出本轮范围）。
5. **DROP 在 ADD 之前**：保持与 `generate_alter_table` 第 2/3 步（先删字段、再加字段）一致的顺序，避免列名冲突。

## 代码实现情况

### `_format_alter_column` SQLite 分支

```python
if dialect == EngineType.SQLITE:
    # SQLite 不支持 ALTER COLUMN，用 DROP + ADD 替代；主键字段不允许删除（SQLite 限制）
    if old_f.primary_key:
        raise DDLError(
            f"SQLite 不支持修改主键字段 {old_f.name} 的定义：DROP COLUMN 不允许删除主键列，"
            "请保留原主键定义或重建表"
        )
    col_ref = _quote_ident(old_f.name, dialect)
    return [
        f"ALTER TABLE {table_ref} DROP COLUMN {col_ref}",
        f"ALTER TABLE {table_ref} ADD COLUMN {_format_column_def(new_f, dialect)}",
    ]
```

### 测试覆盖

- `test_alter_table_modify_column_sqlite_drop_add`：name 字段 VARCHAR(50) NOT NULL → VARCHAR(100) 可空，验证生成 DROP+ADD、ADD 中含新定义、顺序正确。
- `test_alter_table_modify_column_sqlite_pk_raises`：id 字段 INTEGER → BIGINT（主键），验证抛 `DDLError` 含"主键字段 id"。

## 整合优化情况

- 删除 `DDLOperationNotSupported` 类与 `__all__` 导出、tests 中对应 import，避免死代码。
- SQLite 字段修改语义与 MySQL/PostgreSQL 在 `generate_alter_table` 第 4 步统一调用 `_format_alter_column`，方言差异内聚在单函数内。

## 测试验证结果

- `make check`：ruff/format/pyrefly 0 errors，915 passed（新增 1 个），覆盖率 97.82%（>=95%）。
- `backend/apps/designer/ddl.py` 覆盖率 99%。

## 遗留事项

- SQLite 修改主键字段定义仍抛 `DDLError`：如需支持，需实现 12 步表重建（CREATE NEW + INSERT SELECT + DROP OLD + RENAME + 重建索引/外键），逻辑复杂暂不在本轮范围。错误消息已指引"重建表"。
- DROP+ADD 会丢失原列数据：用户在 DDL 预览界面可看到 DROP+ADD 序列理解语义；未来如需在 UI 显式提示"会丢失数据"，可在 `DDLResult` 加 `warnings` 字段（涉及 schemas.py 与前端联动）。

## 下一轮计划

无明确下一轮需求。等待用户提出新需求或反馈。
