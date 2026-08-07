"""DDL 生成器.

按 MySQL/PostgreSQL/SQLite 三种方言生成 ``CREATE TABLE`` / ``ALTER TABLE`` 语句，
供表设计器预览与执行。

设计要点：
- ``generate_create_table(spec, dialect)``: 生成建表 + 索引 + 注释语句
- ``generate_alter_table(old_spec, new_spec, dialect)``: 生成多条 ALTER 语句
- 标识符引用：MySQL 用反引号，PG/SQLite 用双引号
- 自增主键：MySQL ``AUTO_INCREMENT``、SQLite ``AUTOINCREMENT``、PostgreSQL ``SERIAL`` 替换 ``INTEGER``
- 注释：MySQL 内联 ``COMMENT``、PostgreSQL 独立 ``COMMENT ON`` 语句、SQLite 忽略
- 字段修改：MySQL ``MODIFY COLUMN`` 重写整列、PostgreSQL ``ALTER COLUMN`` 拆分多语句、SQLite 用 ``DROP COLUMN`` + ``ADD COLUMN`` 替代（会丢失原列数据，主键字段不允许此操作）
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.datasources.models import EngineType

from .schemas import FieldSpec, ForeignKeySpec, IndexSpec, TableDesignSpec

# MySQL 类型 → PostgreSQL SERIAL 系列映射（用于自增主键类型替换）
_POSTGRES_SERIAL_MAP: dict[str, str] = {
    "INTEGER": "SERIAL",
    "INT": "SERIAL",
    "BIGINT": "BIGSERIAL",
    "SMALLINT": "SMALLSERIAL",
}

# 需要附带长度的类型
_LENGTH_TYPES: frozenset[str] = frozenset({"VARCHAR", "CHAR", "VARBINARY", "BINARY", "NVARCHAR", "NCHAR"})

# 合法的 ON DELETE 行为
_VALID_ON_DELETE: frozenset[str] = frozenset({"CASCADE", "SET NULL", "RESTRICT", "NO ACTION", "SET DEFAULT"})


class DDLError(ValueError):
    """DDL 生成错误（如方言不支持的操作、非法配置）."""


@dataclass(frozen=True)
class DDLResult:
    """DDL 生成结果."""

    statements: tuple[str, ...]


def _validate_dialect(dialect: str) -> str:
    """校验方言合法性并返回."""
    if dialect not in (EngineType.MYSQL, EngineType.POSTGRESQL, EngineType.SQLITE):
        raise DDLError(f"不支持的方言: {dialect}")
    return dialect


def _quote_ident(name: str, dialect: str) -> str:
    """标识符引用（MySQL 用反引号，其他用双引号）."""
    if dialect == EngineType.MYSQL:
        return f"`{name}`"
    return f'"{name}"'


def _format_table_ref(table_name: str, schema_name: str | None, dialect: str) -> str:
    """生成表引用（含 schema 前缀）.

    SQLite 不支持 schema 限定，强制忽略 schema_name。
    """
    if schema_name and dialect != EngineType.SQLITE:
        return f"{_quote_ident(schema_name, dialect)}.{_quote_ident(table_name, dialect)}"
    return _quote_ident(table_name, dialect)


def _escape_sql_string(s: str) -> str:
    """转义 SQL 字符串字面量中的单引号（双写）."""
    return s.replace("'", "''")


def _format_type(field: FieldSpec) -> str:
    """生成字段类型字符串（含长度）."""
    type_upper = field.type.upper()
    if field.length and type_upper in _LENGTH_TYPES:
        return f"{type_upper}({field.length})"
    return type_upper


def _postgres_serial_type(field: FieldSpec) -> str:
    """PostgreSQL 自增主键类型替换（INTEGER → SERIAL）.

    若用户已显式指定 SERIAL 系列类型则原样返回。
    """
    type_upper = field.type.upper()
    if type_upper in _POSTGRES_SERIAL_MAP:
        return _POSTGRES_SERIAL_MAP[type_upper]
    return type_upper


def _format_default(field: FieldSpec) -> str | None:
    """生成 DEFAULT 子句（无则 None）.

    约定 ``default`` 字段为完整 SQL 表达式，原样输出。
    """
    if field.default is None:
        return None
    return f"DEFAULT {field.default}"


def _format_column_def(field: FieldSpec, dialect: str, *, inline_pk: bool = False) -> str:  # noqa: PLR0912
    """生成单字段定义子句.

    Args:
        field: 字段定义。
        dialect: 目标方言。
        inline_pk: 是否内联 PRIMARY KEY 关键字（单列自增主键时为 True，
            SQLite 的 AUTOINCREMENT 必须紧跟 PRIMARY KEY）。
    """
    parts: list[str] = [_quote_ident(field.name, dialect)]

    # 类型处理：PostgreSQL 自增主键用 SERIAL 替换 INTEGER
    if field.autoincrement and field.primary_key and dialect == EngineType.POSTGRESQL:
        parts.append(_postgres_serial_type(field))
    else:
        parts.append(_format_type(field))

    if not field.nullable:
        parts.append("NOT NULL")
    if field.unique and not field.primary_key:
        parts.append("UNIQUE")

    default_clause = _format_default(field)
    if default_clause:
        parts.append(default_clause)

    # 主键与自增关键字
    if field.primary_key and inline_pk:
        # 单列自增主键内联（SQLite AUTOINCREMENT 必须跟在 PRIMARY KEY 后）
        if field.autoincrement:
            if dialect == EngineType.MYSQL:
                parts.append("AUTO_INCREMENT PRIMARY KEY")
            elif dialect == EngineType.SQLITE:
                parts.append("PRIMARY KEY AUTOINCREMENT")
            else:  # PostgreSQL: SERIAL 已隐含序列，仅需 PRIMARY KEY
                parts.append("PRIMARY KEY")
        else:
            parts.append("PRIMARY KEY")
    elif field.autoincrement and field.primary_key and dialect == EngineType.MYSQL:
        # MySQL 复合主键中的自增列仍需 AUTO_INCREMENT（独立 PRIMARY KEY 子句）
        parts.append("AUTO_INCREMENT")

    # 字段注释：仅 MySQL 内联
    if field.comment and dialect == EngineType.MYSQL:
        parts.append(f"COMMENT '{_escape_sql_string(field.comment)}'")

    return " ".join(parts)


def _format_fk_clause(fk: ForeignKeySpec, dialect: str) -> str:
    """生成外键约束子句（CONSTRAINT ... FOREIGN KEY ... REFERENCES ...）."""
    on_delete = fk.on_delete.upper()
    if on_delete not in _VALID_ON_DELETE:
        raise DDLError(f"非法 ON DELETE 行为: {fk.on_delete}")
    name_part = f"CONSTRAINT {_quote_ident(fk.name, dialect)} " if fk.name else ""
    cols = ", ".join(_quote_ident(c, dialect) for c in fk.columns)
    ref_table = _format_table_ref(fk.referred_table, None, dialect)
    ref_cols = ", ".join(_quote_ident(c, dialect) for c in fk.referred_columns)
    return f"{name_part}FOREIGN KEY ({cols}) REFERENCES {ref_table} ({ref_cols}) ON DELETE {on_delete}"


def _format_create_index(idx: IndexSpec, table_ref: str, dialect: str) -> str:
    """生成 CREATE INDEX 语句."""
    unique = "UNIQUE " if idx.unique else ""
    cols = ", ".join(_quote_ident(c, dialect) for c in idx.columns)
    return f"CREATE {unique}INDEX {_quote_ident(idx.name, dialect)} ON {table_ref} ({cols})"


def _format_drop_index(name: str, table_ref: str, dialect: str) -> str:
    """生成 DROP INDEX 语句（MySQL 需要 ON table）."""
    if dialect == EngineType.MYSQL:
        return f"DROP INDEX {_quote_ident(name, dialect)} ON {table_ref}"
    return f"DROP INDEX {_quote_ident(name, dialect)}"


def generate_create_table(spec: TableDesignSpec, dialect: str) -> DDLResult:
    """生成 CREATE TABLE 语句（含独立索引与注释语句）.

    Args:
        spec: 表设计规范。
        dialect: 目标方言（mysql/postgresql/sqlite）。

    Returns:
        DDLResult：包含 CREATE TABLE 与必要的 CREATE INDEX、COMMENT ON 语句。

    Raises:
        DDLError: 方言不支持或配置非法。
    """
    dialect = _validate_dialect(dialect)
    if not spec.fields:
        raise DDLError("表至少需要一个字段")
    table_ref = _format_table_ref(spec.name, spec.schema_name, dialect)

    column_defs: list[str] = []
    pk_cols: list[str] = []
    pk_fields = [f for f in spec.fields if f.primary_key]
    for field in pk_fields:
        pk_cols.append(field.name)
    # 单列主键内联 PRIMARY KEY（SQLite AUTOINCREMENT 必须紧跟 PRIMARY KEY）
    inline_pk = len(pk_cols) == 1
    for field in spec.fields:
        column_defs.append("  " + _format_column_def(field, dialect, inline_pk=inline_pk))

    # 主键约束（仅当不是内联主键时才独立输出；复合主键走此分支）
    if pk_cols and not inline_pk:
        cols_str = ", ".join(_quote_ident(c, dialect) for c in pk_cols)
        column_defs.append(f"  PRIMARY KEY ({cols_str})")

    # 外键约束
    for fk in spec.foreign_keys:
        column_defs.append("  " + _format_fk_clause(fk, dialect))

    statements: list[str] = []
    create_sql = f"CREATE TABLE {table_ref} (\n" + ",\n".join(column_defs) + "\n)"
    statements.append(create_sql)

    # 索引（独立语句）
    for idx in spec.indexes:
        statements.append(_format_create_index(idx, table_ref, dialect))

    # PostgreSQL 表注释与字段注释（独立语句）
    if dialect == EngineType.POSTGRESQL:
        if spec.comment:
            statements.append(f"COMMENT ON TABLE {table_ref} IS '{_escape_sql_string(spec.comment)}'")
        for field in spec.fields:
            if field.comment:
                col_ref = f"{table_ref}.{_quote_ident(field.name, dialect)}"
                statements.append(f"COMMENT ON COLUMN {col_ref} IS '{_escape_sql_string(field.comment)}'")
    elif dialect == EngineType.MYSQL and spec.comment:
        # MySQL 表注释：通过 ALTER TABLE 设置
        statements.append(f"ALTER TABLE {table_ref} COMMENT = '{_escape_sql_string(spec.comment)}'")

    return DDLResult(statements=tuple(statements))


def _field_attrs_changed(old_f: FieldSpec, new_f: FieldSpec) -> bool:
    """比较字段属性是否变化（不含名称）."""
    return (
        old_f.type != new_f.type
        or old_f.length != new_f.length
        or old_f.nullable != new_f.nullable
        or old_f.default != new_f.default
        or old_f.comment != new_f.comment
        or old_f.unique != new_f.unique
        or old_f.primary_key != new_f.primary_key
        or old_f.autoincrement != new_f.autoincrement
    )


def _format_alter_column(
    table_ref: str,
    old_f: FieldSpec,
    new_f: FieldSpec,
    dialect: str,
    *,
    table_name: str = "",
) -> list[str]:
    """生成修改字段定义的 ALTER 语句（不含重命名）.

    - MySQL: 用 MODIFY COLUMN 重写完整列定义
    - PostgreSQL: 拆分为 ALTER COLUMN TYPE / SET NOT NULL / SET DEFAULT 等多条语句
    - SQLite: 不支持直接修改字段定义，用 DROP COLUMN + ADD COLUMN 替代（会丢失原列数据）；
      主键字段不允许此操作（SQLite DROP COLUMN 不允许删除主键列），抛 DDLError；
      ADD COLUMN 不允许 UNIQUE 约束，需用 CREATE UNIQUE INDEX 替代。
    """
    if dialect == EngineType.SQLITE:
        # SQLite 不支持 ALTER COLUMN，用 DROP + ADD 替代；主键字段不允许删除（SQLite 限制）
        if old_f.primary_key:
            raise DDLError(
                f"SQLite 不支持修改主键字段 {old_f.name} 的定义：DROP COLUMN 不允许删除主键列，请保留原主键定义或重建表"
            )
        col_ref = _quote_ident(old_f.name, dialect)
        # SQLite ADD COLUMN 不允许 UNIQUE 约束，需用 CREATE UNIQUE INDEX 替代
        add_field = new_f.model_copy(update={"unique": False})
        stmts = [
            f"ALTER TABLE {table_ref} DROP COLUMN {col_ref}",
            f"ALTER TABLE {table_ref} ADD COLUMN {_format_column_def(add_field, dialect)}",
        ]
        if new_f.unique and not new_f.primary_key:
            # 索引名约定：uq_<表名>_<列名>；命名冲突时由 SQLite 报错，用户可重命名
            idx_name = f"uq_{table_name}_{new_f.name}" if table_name else f"uq_{new_f.name}"
            stmts.append(f"CREATE UNIQUE INDEX {_quote_ident(idx_name, dialect)} ON {table_ref} ({col_ref})")
        return stmts

    if dialect == EngineType.MYSQL:
        # MySQL MODIFY COLUMN 重写完整列定义（包含主键与注释）；主键字段需内联 PRIMARY KEY 保留约束
        return [
            f"ALTER TABLE {table_ref} MODIFY COLUMN {_format_column_def(new_f, dialect, inline_pk=new_f.primary_key)}"
        ]

    # PostgreSQL：拆分为多条 ALTER COLUMN 语句
    stmts: list[str] = []
    col_ref = _quote_ident(new_f.name, dialect)

    # 类型变更（含长度）
    if old_f.type != new_f.type or old_f.length != new_f.length:
        new_type = _postgres_serial_type(new_f) if new_f.autoincrement and new_f.primary_key else _format_type(new_f)
        stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} TYPE {new_type}")

    # 可空性变更
    if old_f.nullable != new_f.nullable:
        if new_f.nullable:
            stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} DROP NOT NULL")
        else:
            stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} SET NOT NULL")

    # 默认值变更
    if old_f.default != new_f.default:
        if new_f.default is None:
            stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} DROP DEFAULT")
        else:
            stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} SET DEFAULT {new_f.default}")

    # 注释变更
    if old_f.comment != new_f.comment and new_f.comment:
        stmts.append(f"COMMENT ON COLUMN {table_ref}.{col_ref} IS '{_escape_sql_string(new_f.comment)}'")

    return stmts


def _index_fks_by_name(fks: list[ForeignKeySpec]) -> dict[str | None, ForeignKeySpec]:
    """以外键 name 为键建立字典（name 为 None 时键为 None）."""
    return {fk.name: fk for fk in fks}


def generate_alter_table(  # noqa: PLR0912
    old_spec: TableDesignSpec,
    new_spec: TableDesignSpec,
    dialect: str,
) -> DDLResult:
    """生成 ALTER TABLE 语句（多条，按差异生成）.

    支持的变更：表重命名、新增/删除/修改字段、新增/删除索引、新增/删除外键、表注释变更。

    Args:
        old_spec: 旧表设计规范。
        new_spec: 新表设计规范。
        dialect: 目标方言。

    Returns:
        DDLResult：包含多条 ALTER / CREATE INDEX / DROP INDEX / COMMENT 语句。

    Raises:
        DDLError: 方言不支持或配置非法。
    """
    dialect = _validate_dialect(dialect)
    statements: list[str] = []
    old_table_ref = _format_table_ref(old_spec.name, old_spec.schema_name, dialect)
    new_table_ref = _format_table_ref(new_spec.name, new_spec.schema_name, dialect)

    # 1. 表重命名（名称或 schema 变化）
    table_renamed = old_spec.name != new_spec.name or old_spec.schema_name != new_spec.schema_name
    if table_renamed:
        if dialect == EngineType.MYSQL:
            statements.append(f"RENAME TABLE {old_table_ref} TO {new_table_ref}")
        else:
            statements.append(f"ALTER TABLE {old_table_ref} RENAME TO {new_table_ref}")

    # 后续操作基于新表名
    table_ref = new_table_ref

    old_fields = {f.name: f for f in old_spec.fields}
    new_fields = {f.name: f for f in new_spec.fields}

    # 2. 删除字段（先删，避免与新字段冲突）
    for name in old_fields.keys() - new_fields.keys():
        statements.append(f"ALTER TABLE {table_ref} DROP COLUMN {_quote_ident(name, dialect)}")

    # 3. 新增字段
    for name in new_fields.keys() - old_fields.keys():
        field = new_fields[name]
        if dialect == EngineType.SQLITE and field.unique and not field.primary_key:
            # SQLite ADD COLUMN 不允许 UNIQUE，需用 CREATE UNIQUE INDEX 替代
            add_field = field.model_copy(update={"unique": False})
            statements.append(f"ALTER TABLE {table_ref} ADD COLUMN {_format_column_def(add_field, dialect)}")
            idx_name = f"uq_{new_spec.name}_{field.name}"
            statements.append(
                f"CREATE UNIQUE INDEX {_quote_ident(idx_name, dialect)} ON {table_ref} ({_quote_ident(field.name, dialect)})"
            )
        else:
            col_def = _format_column_def(field, dialect)
            statements.append(f"ALTER TABLE {table_ref} ADD COLUMN {col_def}")

    # 4. 修改字段定义（类型/可空/默认/注释/约束）
    for name in old_fields.keys() & new_fields.keys():
        old_f = old_fields[name]
        new_f = new_fields[name]
        if _field_attrs_changed(old_f, new_f):
            statements.extend(_format_alter_column(table_ref, old_f, new_f, dialect, table_name=new_spec.name))

    # 5. 索引差异（按名称匹配）
    old_idx = {i.name: i for i in old_spec.indexes}
    new_idx = {i.name: i for i in new_spec.indexes}
    for name in old_idx.keys() - new_idx.keys():
        statements.append(_format_drop_index(name, table_ref, dialect))
    for name in new_idx.keys() - old_idx.keys():
        statements.append(_format_create_index(new_idx[name], table_ref, dialect))

    # 6. 外键差异（按 name 匹配；name 为 None 的外键不参与差异比较）
    old_fks = _index_fks_by_name(old_spec.foreign_keys)
    new_fks = _index_fks_by_name(new_spec.foreign_keys)
    # SQLite 不支持 ALTER TABLE ADD/DROP CONSTRAINT，有名外键变更需重建表（无名外键本来就跳过）
    if dialect == EngineType.SQLITE:
        named_changed = ((old_fks.keys() - new_fks.keys()) | (new_fks.keys() - old_fks.keys())) - {None}
        if named_changed:
            raise DDLError(
                "SQLite 不支持通过 ALTER TABLE 增删外键约束（不支持 ADD/DROP CONSTRAINT），"
                "请删除并重建表或在 CREATE TABLE 时定义外键"
            )
    for name in old_fks.keys() - new_fks.keys():
        if name is None:
            continue  # 无名外键无法精确删除，跳过
        if dialect == EngineType.MYSQL:
            statements.append(f"ALTER TABLE {table_ref} DROP FOREIGN KEY {_quote_ident(name, dialect)}")
        else:
            statements.append(f"ALTER TABLE {table_ref} DROP CONSTRAINT {_quote_ident(name, dialect)}")
    for name in new_fks.keys() - old_fks.keys():
        fk = new_fks[name]
        statements.append(f"ALTER TABLE {table_ref} ADD {_format_fk_clause(fk, dialect)}")

    # 7. 表注释变更
    if old_spec.comment != new_spec.comment:
        if dialect == EngineType.POSTGRESQL and new_spec.comment:
            statements.append(f"COMMENT ON TABLE {table_ref} IS '{_escape_sql_string(new_spec.comment)}'")
        elif dialect == EngineType.MYSQL:
            comment_value = _escape_sql_string(new_spec.comment) if new_spec.comment else ""
            statements.append(f"ALTER TABLE {table_ref} COMMENT = '{comment_value}'")
        # SQLite 不支持表注释，忽略

    return DDLResult(statements=tuple(statements))


def generate_ddl(
    spec: TableDesignSpec,
    dialect: str,
    old_spec: TableDesignSpec | None = None,
) -> DDLResult:
    """统一入口：根据是否传入 old_spec 生成 CREATE 或 ALTER 语句."""
    if old_spec is None:
        return generate_create_table(spec, dialect)
    return generate_alter_table(old_spec, spec, dialect)


__all__ = [
    "DDLError",
    "DDLResult",
    "generate_alter_table",
    "generate_create_table",
    "generate_ddl",
]
