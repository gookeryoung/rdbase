# iter-28 SQLite ADD COLUMN UNIQUE 拆为 CREATE UNIQUE INDEX

## 需求清单

- [x] 修复：SQLite 修改/新增字段加 UNIQUE 时应用回库失败（"Cannot add a UNIQUE column"）

## 迭代目标

用户反馈：「明明没有改主键，为何提示错误，只改字段是不是唯一」。

iter-27 实现 SQLite 修改字段定义用 DROP COLUMN + ADD COLUMN 替代。但 SQLite `ALTER TABLE ADD COLUMN` 文档明确禁止带 UNIQUE 约束的列：执行时报 `sqlite3.OperationalError: Cannot add a UNIQUE column`。导致用户在设计器中给非主键字段加 UNIQUE 后应用回库失败。

本次修复：SQLite 方言下，ADD COLUMN 不输出 UNIQUE 关键字，改为额外生成 `CREATE UNIQUE INDEX` 实现唯一约束（与 SQLite 实践一致——列级 UNIQUE 本质就是隐式唯一索引）。

## 改动文件清单

- [backend/apps/designer/ddl.py](file:///f:/Dev/rdbase/backend/apps/designer/ddl.py)：
  - `_format_alter_column` 新增 `table_name` 参数；SQLite 分支用 `model_copy(update={"unique": False})` 临时去掉 UNIQUE 后再 ADD COLUMN，若 `new_f.unique=True and not new_f.primary_key` 额外生成 `CREATE UNIQUE INDEX uq_<表名>_<列名>`。
  - `generate_alter_table` 第 3 步新增字段同样处理 SQLite UNIQUE：拆为 ADD COLUMN（不含 UNIQUE）+ CREATE UNIQUE INDEX。
  - 第 4 步调用 `_format_alter_column` 时传入 `table_name=new_spec.name` 用于索引命名。
- [tests/test_designer_ddl.py](file:///f:/Dev/rdbase/tests/test_designer_ddl.py)：
  - 新增 `test_alter_table_modify_column_sqlite_unique_to_index`：修改字段加 UNIQUE → DROP+ADD（不含 UNIQUE）+ CREATE UNIQUE INDEX，验证顺序与索引名。
  - 新增 `test_alter_table_add_column_sqlite_unique_to_index`：新增 UNIQUE 字段同样拆为 ADD+INDEX。
  - 新增 `test_alter_table_modify_column_sqlite_drop_unique_no_index`：取消字段 UNIQUE 不应生成 CREATE UNIQUE INDEX（旧索引随 DROP COLUMN 自动删除）。
- [tests/test_designer_drafts_api.py](file:///f:/Dev/rdbase/tests/test_designer_drafts_api.py)：
  - 新增 `test_apply_draft_alter_field_unique_uses_index`：端到端验证反向工程导入 → 改 name 字段 unique=True → 应用回库成功，目标表通过唯一索引实现 UNIQUE 约束。

## 关键决策与依据

1. **CREATE UNIQUE INDEX 而非抛错**：SQLite 列级 UNIQUE 本质是隐式唯一索引（`sqlite_autoindex_<table>_<n>`），用 CREATE UNIQUE INDEX 实现语义等价。抛错会让用户无法通过设计器给现有字段加 UNIQUE，违背 iter-26/27 反向工程应用回库的初衷。
2. **索引名约定 `uq_<表名>_<列名>`**：
   - 与 SQLite 隐式命名 `sqlite_autoindex_<table>_<n>` 区分，用户可见且可管理。
   - 与设计器 IndexSpec 用户自定义命名空间不冲突（用户创建的索引通常命名 `idx_xxx`）。
   - 命名冲突时 SQLite 报错，用户可在索引管理界面重命名或删除旧索引后重试——这是可接受的边界情况，不引入复杂的冲突检测逻辑。
3. **`model_copy(update={"unique": False})` 而非新增 `_format_column_def` 参数**：避免给 `_format_column_def` 增加 `for_add_column` 之类的参数让签名复杂化。临时复制 field 对象去掉 unique 后复用既有格式化逻辑，最小改动。
4. **新增字段（第 3 步）同样修复**：SQLite ADD COLUMN 限制对新增字段一样生效，否则用户在设计器加新字段并勾选 UNIQUE 时也会踩同样坑。两处共用同一处理思路保持一致。
5. **取消 UNIQUE 不需 DROP INDEX**：SQLite `DROP COLUMN` 会自动删除该列上的索引（包括隐式 UNIQUE 索引），所以 `old.unique=True → new.unique=False` 时只需 DROP+ADD（不含 UNIQUE），无需显式 DROP INDEX。文档明确此行为。
6. **主键字段仍抛 DDLError**：iter-27 已处理，本次不动。SQLite DROP COLUMN 不允许删除主键列，无论 new_f.unique 如何。

## 代码实现情况

### `_format_alter_column` SQLite 分支

```python
if dialect == EngineType.SQLITE:
    if old_f.primary_key:
        raise DDLError(...)
    col_ref = _quote_ident(old_f.name, dialect)
    # SQLite ADD COLUMN 不允许 UNIQUE 约束，需用 CREATE UNIQUE INDEX 替代
    add_field = new_f.model_copy(update={"unique": False})
    stmts = [
        f"ALTER TABLE {table_ref} DROP COLUMN {col_ref}",
        f"ALTER TABLE {table_ref} ADD COLUMN {_format_column_def(add_field, dialect)}",
    ]
    if new_f.unique and not new_f.primary_key:
        idx_name = f"uq_{table_name}_{new_f.name}" if table_name else f"uq_{new_f.name}"
        stmts.append(
            f"CREATE UNIQUE INDEX {_quote_ident(idx_name, dialect)} ON {table_ref} ({col_ref})"
        )
    return stmts
```

### `generate_alter_table` 第 3 步新增字段

```python
for name in new_fields.keys() - old_fields.keys():
    field = new_fields[name]
    if dialect == EngineType.SQLITE and field.unique and not field.primary_key:
        add_field = field.model_copy(update={"unique": False})
        statements.append(f"ALTER TABLE {table_ref} ADD COLUMN {_format_column_def(add_field, dialect)}")
        idx_name = f"uq_{new_spec.name}_{field.name}"
        statements.append(
            f"CREATE UNIQUE INDEX {_quote_ident(idx_name, dialect)} ON {table_ref} ({_quote_ident(field.name, dialect)})"
        )
    else:
        col_def = _format_column_def(field, dialect)
        statements.append(f"ALTER TABLE {table_ref} ADD COLUMN {col_def}")
```

### 端到端测试验证

`test_apply_draft_alter_field_unique_uses_index` 实际创建 SQLite 文件库 → 建表 → 草稿 spec 中 name 字段 unique=True → 应用 → 验证：
- 生成 DROP COLUMN + ADD COLUMN（不含 UNIQUE）+ CREATE UNIQUE INDEX
- 目标表 `sa_inspect(engine).get_indexes("users")` 返回的索引中有 `unique=True` 项

## 整合优化情况

- `_format_alter_column` 与 `generate_alter_table` 第 3 步共用同一处理思路（model_copy 去 UNIQUE + CREATE UNIQUE INDEX），方言特殊处理内聚。
- 索引名约定 `uq_<表名>_<列名>` 与 SQLite 隐式 `sqlite_autoindex_*`、用户自定义 `idx_*` 命名空间区分，避免冲突。

## 测试验证结果

- `make check`：ruff/format/pyrefly 0 errors，919 passed（新增 4 个），覆盖率 97.83%（>=95%）。
- `backend/apps/designer/ddl.py` 覆盖率 99%。
- 端到端测试覆盖真实 SQLite 文件库 + HTTP 接口 + 反射验证，确保端到端可用。

## 遗留事项

- 索引名 `uq_<表名>_<列名>` 命名冲突时由 SQLite 报错，用户需手动重命名或删除旧索引。未来如需在 DDL 生成阶段检测冲突，需额外查询目标库现有索引名（涉及 datasource 访问，超出 ddl.py 纯生成职责，暂不实现）。
- 修改字段定义仍会丢失原列数据（DROP+ADD 固有特性，iter-27 已说明）。

## 下一轮计划

无明确下一轮需求。等待用户提出新需求或反馈。
