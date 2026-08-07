"""DDL 生成器单元测试.

覆盖 MySQL/PostgreSQL/SQLite 三方言的 CREATE TABLE 与 ALTER TABLE 生成。
"""

from __future__ import annotations

import pytest
from apps.datasources.models import EngineType
from apps.designer.ddl import (
    DDLError,
    generate_alter_table,
    generate_create_table,
    generate_ddl,
)
from apps.designer.schemas import FieldSpec, ForeignKeySpec, IndexSpec, TableDesignSpec


def _make_spec(  # noqa: PLR0913
    *,
    name: str = "users",
    fields: list[FieldSpec] | None = None,
    indexes: list[IndexSpec] | None = None,
    foreign_keys: list[ForeignKeySpec] | None = None,
    comment: str | None = None,
    schema_name: str | None = None,
) -> TableDesignSpec:
    """构造测试用 TableDesignSpec."""
    if fields is None:
        fields = [
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True, autoincrement=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
            FieldSpec(name="email", type="VARCHAR", length=100, nullable=True, unique=True),
        ]
    return TableDesignSpec(
        name=name,
        schema_name=schema_name,
        comment=comment,
        fields=fields,
        indexes=indexes or [],
        foreign_keys=foreign_keys or [],
    )


# ---------- CREATE TABLE 基本生成 ----------


def test_create_table_sqlite_basic() -> None:
    """SQLite 基本建表语句：双引号标识符，自增主键内联 PRIMARY KEY AUTOINCREMENT."""
    spec = _make_spec()
    result = generate_create_table(spec, EngineType.SQLITE)
    stmt = result.statements[0]
    assert stmt.startswith('CREATE TABLE "users" (')
    # SQLite AUTOINCREMENT 必须跟在 PRIMARY KEY 后
    assert '"id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT' in stmt
    assert '"name" VARCHAR(50) NOT NULL' in stmt
    assert '"email" VARCHAR(100) UNIQUE' in stmt
    # 内联主键时不再有独立 PRIMARY KEY 子句
    assert 'PRIMARY KEY ("id")' not in stmt


def test_create_table_mysql_basic() -> None:
    """MySQL 建表语句：反引号标识符，自增主键内联 AUTO_INCREMENT PRIMARY KEY."""
    spec = _make_spec()
    result = generate_create_table(spec, EngineType.MYSQL)
    stmt = result.statements[0]
    assert stmt.startswith("CREATE TABLE `users` (")
    assert "`id` INTEGER NOT NULL AUTO_INCREMENT PRIMARY KEY" in stmt
    assert "`name` VARCHAR(50) NOT NULL" in stmt
    assert "`email` VARCHAR(100) UNIQUE" in stmt
    # 内联主键时不再有独立 PRIMARY KEY 子句
    assert "PRIMARY KEY (`id`)" not in stmt


def test_create_table_postgresql_basic() -> None:
    """PostgreSQL 建表语句：INTEGER 自增主键替换为 SERIAL，内联 PRIMARY KEY."""
    spec = _make_spec()
    result = generate_create_table(spec, EngineType.POSTGRESQL)
    stmt = result.statements[0]
    assert stmt.startswith('CREATE TABLE "users" (')
    # PostgreSQL 自增主键用 SERIAL 替换 INTEGER，不输出 AUTO_INCREMENT 关键字
    assert '"id" SERIAL NOT NULL PRIMARY KEY' in stmt
    assert "AUTO_INCREMENT" not in stmt
    assert "AUTOINCREMENT" not in stmt


def test_create_table_postgresql_bigint_serial() -> None:
    """PostgreSQL BIGINT 自增主键应替换为 BIGSERIAL，内联 PRIMARY KEY."""
    fields = [
        FieldSpec(name="id", type="BIGINT", nullable=False, primary_key=True, autoincrement=True),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.POSTGRESQL)
    assert '"id" BIGSERIAL NOT NULL PRIMARY KEY' in result.statements[0]


def test_create_table_with_schema_prefix() -> None:
    """非 SQLite 方言应输出 schema 前缀；SQLite 强制忽略 schema."""
    spec = _make_spec(schema_name="public")
    mysql_result = generate_create_table(spec, EngineType.MYSQL)
    assert "CREATE TABLE `public`.`users`" in mysql_result.statements[0]
    pg_result = generate_create_table(spec, EngineType.POSTGRESQL)
    assert 'CREATE TABLE "public"."users"' in pg_result.statements[0]
    sqlite_result = generate_create_table(spec, EngineType.SQLITE)
    assert 'CREATE TABLE "users"' in sqlite_result.statements[0]
    assert '"public"' not in sqlite_result.statements[0]


# ---------- DEFAULT 与 COMMENT ----------


def test_create_table_default_value() -> None:
    """DEFAULT 子句应原样输出."""
    fields = [
        FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
        FieldSpec(name="age", type="INTEGER", nullable=True, default="0"),
        FieldSpec(name="created_at", type="TIMESTAMP", nullable=True, default="CURRENT_TIMESTAMP"),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.SQLITE)
    stmt = result.statements[0]
    assert '"age" INTEGER DEFAULT 0' in stmt
    assert '"created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP' in stmt


def test_create_table_mysql_column_comment() -> None:
    """MySQL 字段注释应内联输出."""
    fields = [
        FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
        FieldSpec(name="name", type="VARCHAR", length=50, nullable=False, comment="用户名"),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.MYSQL)
    stmt = result.statements[0]
    assert "COMMENT '用户名'" in stmt


def test_create_table_postgresql_column_comment() -> None:
    """PostgreSQL 字段注释应作为独立 COMMENT ON COLUMN 语句."""
    fields = [
        FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
        FieldSpec(name="name", type="VARCHAR", length=50, nullable=False, comment="用户名"),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.POSTGRESQL)
    comment_stmt = next(s for s in result.statements if s.startswith("COMMENT ON COLUMN"))
    assert "'用户名'" in comment_stmt
    assert '"users"."name"' in comment_stmt


def test_create_table_sqlite_ignores_comment() -> None:
    """SQLite 不支持注释，应忽略."""
    fields = [
        FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True, comment="主键"),
    ]
    spec = _make_spec(fields=fields, comment="表注释")
    result = generate_create_table(spec, EngineType.SQLITE)
    for stmt in result.statements:
        assert "COMMENT" not in stmt


def test_create_table_mysql_table_comment() -> None:
    """MySQL 表注释通过 ALTER TABLE 设置."""
    spec = _make_spec(comment="用户表")
    result = generate_create_table(spec, EngineType.MYSQL)
    comment_stmt = next(s for s in result.statements if "COMMENT =" in s)
    assert "'用户表'" in comment_stmt


def test_create_table_postgresql_table_comment() -> None:
    """PostgreSQL 表注释作为独立 COMMENT ON TABLE 语句."""
    spec = _make_spec(comment="用户表")
    result = generate_create_table(spec, EngineType.POSTGRESQL)
    comment_stmt = next(s for s in result.statements if s.startswith("COMMENT ON TABLE"))
    assert "'用户表'" in comment_stmt


def test_create_table_comment_escaping() -> None:
    """注释中的单引号应被转义（双写）."""
    spec = _make_spec(comment="it's a test")
    result = generate_create_table(spec, EngineType.MYSQL)
    comment_stmt = next(s for s in result.statements if "COMMENT =" in s)
    assert "'it''s a test'" in comment_stmt


# ---------- 外键与索引 ----------


def test_create_table_with_foreign_key() -> None:
    """外键约束应正确生成."""
    fields = [
        FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
        FieldSpec(name="user_id", type="INTEGER", nullable=False),
    ]
    fks = [
        ForeignKeySpec(
            name="fk_posts_user",
            columns=["user_id"],
            referred_table="users",
            referred_columns=["id"],
            on_delete="CASCADE",
        )
    ]
    spec = _make_spec(name="posts", fields=fields, foreign_keys=fks)
    result = generate_create_table(spec, EngineType.MYSQL)
    stmt = result.statements[0]
    assert "CONSTRAINT `fk_posts_user` FOREIGN KEY (`user_id`)" in stmt
    assert "REFERENCES `users` (`id`)" in stmt
    assert "ON DELETE CASCADE" in stmt


def test_create_table_with_index() -> None:
    """索引应作为独立 CREATE INDEX 语句生成."""
    indexes = [
        IndexSpec(name="idx_name", columns=["name"], unique=False),
        IndexSpec(name="idx_email", columns=["email"], unique=True),
    ]
    spec = _make_spec(indexes=indexes)
    result = generate_create_table(spec, EngineType.SQLITE)
    idx_stmts = [s for s in result.statements if s.startswith("CREATE")]
    assert any("CREATE INDEX" in s and "idx_name" in s for s in idx_stmts)
    assert any("CREATE UNIQUE INDEX" in s and "idx_email" in s for s in idx_stmts)


def test_create_table_with_composite_pk() -> None:
    """复合主键应在 PRIMARY KEY 子句中列出所有列."""
    fields = [
        FieldSpec(name="user_id", type="INTEGER", nullable=False, primary_key=True),
        FieldSpec(name="role_id", type="INTEGER", nullable=False, primary_key=True),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.SQLITE)
    stmt = result.statements[0]
    assert 'PRIMARY KEY ("user_id", "role_id")' in stmt


# ---------- 错误分支 ----------


def test_create_table_unsupported_dialect_raises() -> None:
    """不支持的方言应抛 DDLError."""
    spec = _make_spec()
    with pytest.raises(DDLError, match="不支持的方言"):
        generate_create_table(spec, "oracle")


def test_create_table_empty_fields_raises() -> None:
    """空字段列表应抛 DDLError."""
    spec = _make_spec(fields=[])
    with pytest.raises(DDLError, match="至少需要一个字段"):
        generate_create_table(spec, EngineType.SQLITE)


def test_create_table_invalid_on_delete_raises() -> None:
    """非法 ON DELETE 行为应抛 DDLError."""
    fks = [
        ForeignKeySpec(
            name="fk_x",
            columns=["user_id"],
            referred_table="users",
            referred_columns=["id"],
            on_delete="INVALID",
        )
    ]
    spec = _make_spec(fields=[FieldSpec(name="id", type="INTEGER", primary_key=True)], foreign_keys=fks)
    with pytest.raises(DDLError, match="非法 ON DELETE"):
        generate_create_table(spec, EngineType.SQLITE)


# ---------- ALTER TABLE ----------


def test_alter_table_rename_sqlite() -> None:
    """SQLite 表重命名用 ALTER TABLE ... RENAME TO."""
    old = _make_spec(name="users")
    new = _make_spec(name="members")
    result = generate_alter_table(old, new, EngineType.SQLITE)
    assert any("RENAME TO" in s for s in result.statements)
    assert result.statements[0].startswith('ALTER TABLE "users" RENAME TO "members"')


def test_alter_table_rename_mysql() -> None:
    """MySQL 表重命名用 RENAME TABLE."""
    old = _make_spec(name="users")
    new = _make_spec(name="members")
    result = generate_alter_table(old, new, EngineType.MYSQL)
    assert result.statements[0].startswith("RENAME TABLE `users` TO `members`")


def test_alter_table_add_column() -> None:
    """新增字段应生成 ADD COLUMN."""
    old = _make_spec(fields=[FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True)])
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
        ]
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    add_stmt = next(s for s in result.statements if "ADD COLUMN" in s)
    assert '"name" VARCHAR(50) NOT NULL' in add_stmt


def test_alter_table_drop_column() -> None:
    """删除字段应生成 DROP COLUMN."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
        ]
    )
    new = _make_spec(fields=[FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True)])
    result = generate_alter_table(old, new, EngineType.SQLITE)
    drop_stmt = next(s for s in result.statements if "DROP COLUMN" in s)
    assert '"name"' in drop_stmt


def test_alter_table_modify_column_mysql() -> None:
    """MySQL 修改字段用 MODIFY COLUMN 重写整列."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=100, nullable=True),
        ]
    )
    result = generate_alter_table(old, new, EngineType.MYSQL)
    modify_stmt = next(s for s in result.statements if "MODIFY COLUMN" in s)
    assert "`name` VARCHAR(100)" in modify_stmt


def test_alter_table_modify_column_postgresql_split() -> None:
    """PostgreSQL 修改字段应拆分为多条 ALTER COLUMN 语句."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False, default="'x'"),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=100, nullable=True, default=None),
        ]
    )
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    type_stmt = next(s for s in result.statements if "ALTER COLUMN" in s and "TYPE" in s)
    assert "VARCHAR(100)" in type_stmt
    null_stmt = next(s for s in result.statements if "DROP NOT NULL" in s)
    assert '"name"' in null_stmt
    default_stmt = next(s for s in result.statements if "DROP DEFAULT" in s)
    assert '"name"' in default_stmt


def test_alter_table_modify_column_sqlite_drop_add() -> None:
    """SQLite 修改字段定义用 DROP COLUMN + ADD COLUMN 替代（非主键字段）."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=100, nullable=True),
        ]
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    drop_stmt = next(s for s in result.statements if "DROP COLUMN" in s)
    add_stmt = next(s for s in result.statements if "ADD COLUMN" in s)
    assert '"name"' in drop_stmt
    assert '"name" VARCHAR(100)' in add_stmt
    # 新字段定义按 new_spec 重写：可空
    assert "NOT NULL" not in add_stmt
    # DROP 在 ADD 之前
    assert result.statements.index(drop_stmt) < result.statements.index(add_stmt)


def test_alter_table_modify_column_sqlite_pk_raises() -> None:
    """SQLite 修改主键字段定义应抛 DDLError（DROP COLUMN 不允许删除主键列）."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True, autoincrement=True),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="BIGINT", nullable=False, primary_key=True, autoincrement=True),
        ]
    )
    with pytest.raises(DDLError, match="主键字段 id"):
        generate_alter_table(old, new, EngineType.SQLITE)


def test_alter_table_modify_column_sqlite_unique_to_index() -> None:
    """SQLite 修改字段加 UNIQUE 应拆为 DROP+ADD（不含 UNIQUE）+ CREATE UNIQUE INDEX."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="email", type="VARCHAR", length=100, nullable=True, unique=False),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="email", type="VARCHAR", length=100, nullable=True, unique=True),
        ]
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    add_stmt = next(s for s in result.statements if "ADD COLUMN" in s)
    # ADD COLUMN 不含 UNIQUE 关键字（SQLite ADD COLUMN 限制）
    assert "UNIQUE" not in add_stmt
    # 额外生成 CREATE UNIQUE INDEX，索引名约定 uq_<表名>_<列名>
    idx_stmt = next(s for s in result.statements if "CREATE UNIQUE INDEX" in s)
    assert "uq_users_email" in idx_stmt
    assert '"email"' in idx_stmt
    # CREATE UNIQUE INDEX 在 ADD COLUMN 之后
    assert result.statements.index(idx_stmt) > result.statements.index(add_stmt)


def test_alter_table_add_column_sqlite_unique_to_index() -> None:
    """SQLite 新增 UNIQUE 字段应拆为 ADD COLUMN（不含 UNIQUE）+ CREATE UNIQUE INDEX."""
    old = _make_spec(fields=[FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True)])
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="email", type="VARCHAR", length=100, nullable=True, unique=True),
        ]
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    add_stmt = next(s for s in result.statements if "ADD COLUMN" in s)
    assert "UNIQUE" not in add_stmt
    idx_stmt = next(s for s in result.statements if "CREATE UNIQUE INDEX" in s)
    assert "uq_users_email" in idx_stmt


def test_alter_table_modify_column_sqlite_drop_unique_no_index() -> None:
    """SQLite 取消字段 UNIQUE 不应生成 CREATE UNIQUE INDEX（旧索引随 DROP COLUMN 删除）."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="email", type="VARCHAR", length=100, nullable=True, unique=True),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="email", type="VARCHAR", length=200, nullable=True, unique=False),
        ]
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    add_stmt = next(s for s in result.statements if "ADD COLUMN" in s)
    assert "UNIQUE" not in add_stmt
    # new_f.unique=False，不应生成 CREATE UNIQUE INDEX
    assert not any("CREATE UNIQUE INDEX" in s for s in result.statements)


def test_alter_table_add_drop_index() -> None:
    """索引差异应生成 CREATE INDEX / DROP INDEX."""
    old = _make_spec(
        fields=[FieldSpec(name="id", type="INTEGER", primary_key=True)],
        indexes=[IndexSpec(name="idx_a", columns=["name"])],
    )
    new = _make_spec(
        fields=[FieldSpec(name="id", type="INTEGER", primary_key=True)],
        indexes=[IndexSpec(name="idx_b", columns=["email"])],
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    drop_stmt = next(s for s in result.statements if s.startswith("DROP INDEX"))
    assert '"idx_a"' in drop_stmt
    create_stmt = next(s for s in result.statements if s.startswith("CREATE INDEX"))
    assert '"idx_b"' in create_stmt


def test_alter_table_drop_index_mysql_with_on_table() -> None:
    """MySQL DROP INDEX 应包含 ON table 子句."""
    old = _make_spec(
        fields=[FieldSpec(name="id", type="INTEGER", primary_key=True)],
        indexes=[IndexSpec(name="idx_a", columns=["name"])],
    )
    new = _make_spec(
        fields=[FieldSpec(name="id", type="INTEGER", primary_key=True)],
        indexes=[],
    )
    result = generate_alter_table(old, new, EngineType.MYSQL)
    drop_stmt = next(s for s in result.statements if s.startswith("DROP INDEX"))
    assert "ON `users`" in drop_stmt


def test_alter_table_add_drop_foreign_key() -> None:
    """外键差异应生成 ADD CONSTRAINT / DROP CONSTRAINT."""
    old_fk = ForeignKeySpec(
        name="fk_old",
        columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    new_fk = ForeignKeySpec(
        name="fk_new",
        columns=["user_id"],
        referred_table="members",
        referred_columns=["id"],
    )
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[old_fk],
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[new_fk],
    )
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    drop_stmt = next(s for s in result.statements if "DROP CONSTRAINT" in s)
    assert '"fk_old"' in drop_stmt
    add_stmt = next(s for s in result.statements if "ADD CONSTRAINT" in s or "FOREIGN KEY" in s)
    assert "fk_new" in add_stmt


def test_alter_table_drop_foreign_key_mysql() -> None:
    """MySQL 删除外键用 DROP FOREIGN KEY."""
    old_fk = ForeignKeySpec(
        name="fk_old",
        columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[old_fk],
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[],
    )
    result = generate_alter_table(old, new, EngineType.MYSQL)
    drop_stmt = next(s for s in result.statements if "DROP FOREIGN KEY" in s)
    assert "`fk_old`" in drop_stmt


def test_alter_table_no_changes_returns_empty() -> None:
    """无变更应返回空语句列表."""
    spec = _make_spec()
    result = generate_alter_table(spec, spec, EngineType.SQLITE)
    assert len(result.statements) == 0


def test_alter_table_table_comment_change() -> None:
    """表注释变更应生成对应语句."""
    old = _make_spec(comment="旧注释")
    new = _make_spec(comment="新注释")
    # PostgreSQL
    pg_result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    assert any("COMMENT ON TABLE" in s and "新注释" in s for s in pg_result.statements)
    # MySQL
    mysql_result = generate_alter_table(old, new, EngineType.MYSQL)
    assert any("COMMENT =" in s and "新注释" in s for s in mysql_result.statements)


def test_alter_table_table_comment_cleared_mysql() -> None:
    """MySQL 表注释从有到无应生成空注释语句."""
    old = _make_spec(comment="旧注释")
    new = _make_spec(comment=None)
    result = generate_alter_table(old, new, EngineType.MYSQL)
    assert any("COMMENT = ''" in s for s in result.statements)


def test_alter_table_table_comment_cleared_postgresql() -> None:
    """PostgreSQL 表注释清空应跳过（PG 不支持空注释，SQLite 也不支持）."""
    old = _make_spec(comment="旧注释")
    new = _make_spec(comment=None)
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    # PG 清空注释时不生成语句（new_spec.comment 为 None/空，跳过 COMMENT ON TABLE 分支）
    assert not any("COMMENT ON TABLE" in s for s in result.statements)


def test_create_table_postgresql_explicit_serial_type() -> None:
    """PostgreSQL 用户显式指定 SERIAL 类型应原样输出（不做 INTEGER→SERIAL 替换）."""
    fields = [
        FieldSpec(name="id", type="SERIAL", nullable=False, primary_key=True, autoincrement=True),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.POSTGRESQL)
    assert '"id" SERIAL NOT NULL PRIMARY KEY' in result.statements[0]


def test_alter_table_postgresql_set_not_null() -> None:
    """PostgreSQL 字段从 nullable 变为 NOT NULL 应生成 SET NOT NULL."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=True),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False),
        ]
    )
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    assert any("SET NOT NULL" in s for s in result.statements)


def test_alter_table_postgresql_set_default() -> None:
    """PostgreSQL 字段新增默认值应生成 SET DEFAULT."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="age", type="INTEGER", nullable=True, default=None),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="age", type="INTEGER", nullable=True, default="0"),
        ]
    )
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    assert any("SET DEFAULT 0" in s for s in result.statements)


def test_alter_table_postgresql_column_comment_added() -> None:
    """PostgreSQL 字段新增注释应生成 COMMENT ON COLUMN."""
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False, comment=None),
        ]
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", nullable=False, primary_key=True),
            FieldSpec(name="name", type="VARCHAR", length=50, nullable=False, comment="用户名"),
        ]
    )
    result = generate_alter_table(old, new, EngineType.POSTGRESQL)
    assert any("COMMENT ON COLUMN" in s and "用户名" in s for s in result.statements)


def test_alter_table_unnamed_fk_skipped() -> None:
    """无名外键差异应跳过（无法精确删除）."""
    old_fk = ForeignKeySpec(
        name=None,
        columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    old = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[old_fk],
    )
    new = _make_spec(
        fields=[
            FieldSpec(name="id", type="INTEGER", primary_key=True),
            FieldSpec(name="user_id", type="INTEGER"),
        ],
        foreign_keys=[],
    )
    result = generate_alter_table(old, new, EngineType.SQLITE)
    # 无名外键不生成 DROP 语句
    assert not any("DROP CONSTRAINT" in s or "DROP FOREIGN KEY" in s for s in result.statements)


def test_create_table_single_non_autoincrement_pk_inline() -> None:
    """单列非自增主键应内联 PRIMARY KEY."""
    fields = [
        FieldSpec(name="id", type="VARCHAR", length=36, nullable=False, primary_key=True, autoincrement=False),
    ]
    spec = _make_spec(fields=fields)
    result = generate_create_table(spec, EngineType.SQLITE)
    stmt = result.statements[0]
    assert '"id" VARCHAR(36) NOT NULL PRIMARY KEY' in stmt


# ---------- generate_ddl 统一入口 ----------


def test_generate_ddl_create_when_no_old_spec() -> None:
    """无 old_spec 时生成 CREATE."""
    spec = _make_spec()
    result = generate_ddl(spec, EngineType.SQLITE)
    assert result.statements[0].startswith("CREATE TABLE")


def test_generate_ddl_alter_when_old_spec_provided() -> None:
    """传入 old_spec 时生成 ALTER."""
    old = _make_spec(name="users")
    new = _make_spec(name="members")
    result = generate_ddl(new, EngineType.SQLITE, old_spec=old)
    assert any("RENAME TO" in s for s in result.statements)


def test_generate_ddl_unsupported_dialect_raises() -> None:
    """不支持的方言应抛 DDLError."""
    spec = _make_spec()
    with pytest.raises(DDLError):
        generate_ddl(spec, "oracle")
