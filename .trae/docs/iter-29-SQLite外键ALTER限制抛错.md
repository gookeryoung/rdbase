# iter-29 SQLite 外键 ALTER 限制抛错

## 需求清单

- [x] SQLite 方言下增删外键约束提前抛 DDLError，避免生成不可执行语句

## 迭代目标

iter-28 修复 UNIQUE 限制后，继续完善 SQLite ALTER 链路：SQLite 不支持 `ALTER TABLE ADD CONSTRAINT` / `DROP CONSTRAINT`，但 `generate_alter_table` 第 6 步对 SQLite 也生成 `DROP CONSTRAINT` / `ADD CONSTRAINT` 语句，执行时会失败。本次对 SQLite 方言下的有名外键变更提前抛 DDLError，给出清晰错误消息指引"删除并重建表或在 CREATE TABLE 时定义外键"。

## 改动文件清单

- [backend/apps/designer/ddl.py](file:///f:/Dev/rdbase/backend/apps/designer/ddl.py#L405-L417)：`generate_alter_table` 第 6 步外键差异处理前，SQLite 方言下检测有名外键变更（新增或删除），抛 DDLError。无名外键（name=None）保持既有跳过行为。
- [tests/test_designer_ddl.py](file:///f:/Dev/rdbase/tests/test_designer_ddl.py#L464-L513)：
  - 新增 `test_alter_table_sqlite_add_foreign_key_raises`：SQLite 新增有名外键抛 DDLError。
  - 新增 `test_alter_table_sqlite_drop_foreign_key_raises`：SQLite 删除有名外键抛 DDLError。

## 关键决策与依据

1. **仅对有名外键抛错**：既有 `test_alter_table_unnamed_fk_skipped` 期望无名外键（name=None）被跳过（无法精确删除）。SQLite 检查逻辑用 `keys - {None}` 排除无名外键，保持兼容。
2. **不实现 12 步表重建**：SQLite 增删外键需重建表（CREATE NEW + INSERT SELECT + DROP OLD + RENAME + 重建索引/外键），逻辑复杂且易出错（列映射、约束保留、数据迁移）。错误消息指引"删除并重建表或在 CREATE TABLE 时定义外键"——后者是 SQLite 推荐做法，用户可在设计器中删除草稿重建。
3. **NOT NULL 无 DEFAULT / CURRENT_TIME 限制未在生成阶段抛错**：实测 SQLite 对**空表** ADD COLUMN NOT NULL 无 DEFAULT 与 DEFAULT CURRENT_TIMESTAMP 均允许，仅对**有数据表**报错。ddl.py 无法预知目标表是否有数据，生成阶段抛错会破坏空表场景。让 SQLite 执行时报错（"Cannot add a NOT NULL column with default value NULL" / "Cannot add a column with non-constant default"），错误消息明确，用户能理解。
4. **外键限制是硬性的**：SQLite 文档明确 ALTER TABLE 不支持 ADD/DROP CONSTRAINT，无论表是否有数据。提前抛错避免生成不可执行语句，用户体验优于"预览通过但执行失败"。

## 代码实现情况

### `generate_alter_table` 第 6 步 SQLite 外键检查

```python
# 6. 外键差异（按 name 匹配；name 为 None 的外键不参与差异比较）
old_fks = _index_fks_by_name(old_spec.foreign_keys)
new_fks = _index_fks_by_name(new_spec.foreign_keys)
# SQLite 不支持 ALTER TABLE ADD/DROP CONSTRAINT，有名外键变更需重建表（无名外键本来就跳过）
if dialect == EngineType.SQLITE:
    named_changed = (
        (old_fks.keys() - new_fks.keys()) | (new_fks.keys() - old_fks.keys())
    ) - {None}
    if named_changed:
        raise DDLError(
            "SQLite 不支持通过 ALTER TABLE 增删外键约束（不支持 ADD/DROP CONSTRAINT），"
            "请删除并重建表或在 CREATE TABLE 时定义外键"
        )
```

## 整合优化情况

- SQLite ALTER 限制处理分布在 `generate_alter_table` 各步骤：
  - 第 3 步新增字段：UNIQUE 拆为 CREATE UNIQUE INDEX（iter-28）
  - 第 4 步修改字段：DROP+ADD 替代 MODIFY（iter-27），主键抛错（iter-27），UNIQUE 拆 CREATE UNIQUE INDEX（iter-28）
  - 第 6 步外键：有名变更抛错（iter-29）
- 各步骤方言特殊处理内聚，不互相干扰。

## 测试验证结果

- `make check`：ruff/format/pyrefly 0 errors，921 passed（新增 2 个），覆盖率 97.83%（>=95%）。
- `backend/apps/designer/ddl.py` 覆盖率 99%。

## 遗留事项

- SQLite 增删外键仍需用户手动重建表。未来如需支持，需实现 12 步表重建流程（CREATE NEW + INSERT SELECT + DROP OLD + RENAME + 重建索引/外键），逻辑复杂暂不在本轮范围。
- SQLite ADD COLUMN NOT NULL 无 DEFAULT / DEFAULT CURRENT_TIMESTAMP 对有数据表会执行失败，由 SQLite 原始错误消息提示用户。未来如需在 UI 显式提示，可在 `DDLResult` 加 `warnings` 字段（涉及 schemas.py 与前端联动）。

## 下一轮计划

无明确下一轮需求。等待用户提出新需求或反馈。
