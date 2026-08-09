"""seed_sample_data 管理命令测试.

覆盖命令的全流程、幂等性、错误分支与路径解析逻辑：
- 正常执行：创建 3 个 Dataset + 3 张业务表各 30 行；
- 幂等再执行：Dataset 全部跳过、业务表行数不变（INSERT OR IGNORE）；
- 数据源不存在：SystemExit；
- 数据库文件不存在：SystemExit；
- 相对路径解析：相对路径基于 BASE_DIR.parent 解析为绝对路径。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from io import StringIO
from pathlib import Path

import pytest
from apps.accounts.models import Role, User
from apps.datasources.models import Dataset, DataSource, EngineType
from django.core.management import call_command


def _make_sample_db(db_path: Path) -> None:
    """创建带 users/products/orders 三张表的 SQLite 文件."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(50) NOT NULL, "
            "email VARCHAR(100), "
            "age INTEGER DEFAULT 0)"
        )
        cur.execute(
            "CREATE TABLE products ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(100) NOT NULL, "
            "price REAL DEFAULT 0, "
            "stock INTEGER DEFAULT 0)"
        )
        cur.execute(
            "CREATE TABLE orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, "
            "product_id INTEGER, "
            "quantity INTEGER DEFAULT 1, "
            "created_at VARCHAR(32))"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def sample_datasource(tmp_path: Path, admin_user: User) -> DataSource:
    """构造 sample_demo 数据源 + 真实 SQLite 文件（含空业务表）."""
    db_file = tmp_path / "sample.db"
    _make_sample_db(db_file)
    return DataSource.objects.create(
        name="sample_demo",
        engine=EngineType.SQLITE,
        database=str(db_file.resolve()),
        created_by=admin_user,
    )


@pytest.mark.django_db
def test_seed_creates_datasets_and_rows(sample_datasource: DataSource) -> None:
    """执行命令应创建 3 个 Dataset 并向业务表各插入 30 行."""
    out = StringIO()
    call_command("seed_sample_data", stdout=out)
    output = out.getvalue()

    # 输出摘要
    assert "目标数据源" in output
    assert "业务表数据已就绪" in output
    assert "users=30" in output
    assert "products=30" in output
    assert "orders=30" in output
    assert "Dataset 配置" in output
    assert "新建 3 个" in output
    assert "跳过 0 个" in output

    # Dataset 配置
    assert Dataset.objects.count() == 3
    slugs = set(Dataset.objects.values_list("slug", flat=True))
    assert slugs == {"user-profiles", "product-catalog", "order-records"}

    # 列级裁剪配置
    user_ds = Dataset.objects.get(slug="user-profiles")
    assert user_ds.fields_whitelist == ["id", "name", "age"]
    assert user_ds.filter_expression == {}
    assert user_ds.table_name == "users"
    assert user_ds.datasource_id == sample_datasource.pk
    assert user_ds.is_active is True
    assert user_ds.owner_id is not None

    # 行级过滤配置
    product_ds = Dataset.objects.get(slug="product-catalog")
    assert product_ds.fields_whitelist == ["id", "name", "price", "stock"]
    assert product_ds.filter_expression == {"stock": {"op": "gt", "val": 0}}
    assert product_ds.table_name == "products"

    # 全字段开放
    order_ds = Dataset.objects.get(slug="order-records")
    assert order_ds.fields_whitelist == ["id", "user_id", "product_id", "quantity", "created_at"]
    assert order_ds.filter_expression == {}
    assert order_ds.table_name == "orders"

    # 业务表行数
    conn = sqlite3.connect(str(Path(sample_datasource.database).resolve()))
    try:
        cur = conn.cursor()
        for table in ("users", "products", "orders"):
            assert cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 30
    finally:
        conn.close()


@pytest.mark.django_db
def test_seed_idempotent_skips_existing_datasets(sample_datasource: DataSource) -> None:
    """再次执行应跳过全部 Dataset，业务表行数仍为 30（INSERT OR IGNORE）."""
    call_command("seed_sample_data", stdout=StringIO())

    out = StringIO()
    call_command("seed_sample_data", stdout=out)
    output = out.getvalue()

    assert "新建 0 个" in output
    assert "跳过 3 个" in output
    assert Dataset.objects.count() == 3

    # 业务表行数不变
    conn = sqlite3.connect(str(Path(sample_datasource.database).resolve()))
    try:
        cur = conn.cursor()
        for table in ("users", "products", "orders"):
            assert cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 30
    finally:
        conn.close()


@pytest.mark.django_db
def test_seed_raises_when_datasource_missing(db: object) -> None:
    """未创建 sample_demo 数据源时应以 SystemExit 退出."""
    assert not DataSource.objects.filter(name="sample_demo").exists()
    with pytest.raises(SystemExit) as exc:
        call_command("seed_sample_data", stdout=StringIO())
    assert "sample_demo" in str(exc.value)


@pytest.mark.django_db
def test_seed_raises_when_db_file_missing(db: object) -> None:
    """数据源指向不存在的数据库文件时应以 SystemExit 退出."""
    DataSource.objects.create(
        name="sample_demo",
        engine=EngineType.SQLITE,
        database="/nonexistent/path/xyz_rdbase.db",
    )
    with pytest.raises(SystemExit) as exc:
        call_command("seed_sample_data", stdout=StringIO())
    assert "数据库文件不存在" in str(exc.value)


@pytest.mark.django_db
def test_seed_resolves_relative_db_path(
    tmp_path: Path,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相对路径应基于 BASE_DIR.parent 解析为绝对路径."""
    # 将 BASE_DIR 临时指向 tmp_path/backend，使 BASE_DIR.parent == tmp_path
    from django.conf import settings

    fake_backend = tmp_path / "backend"
    fake_backend.mkdir()
    monkeypatch.setattr(settings, "BASE_DIR", fake_backend, raising=True)

    db_file = tmp_path / "sample.db"
    _make_sample_db(db_file)

    DataSource.objects.create(
        name="sample_demo",
        engine=EngineType.SQLITE,
        database="sample.db",  # 相对路径
    )

    out = StringIO()
    call_command("seed_sample_data", stdout=out)
    assert "业务表数据已就绪" in out.getvalue()
    assert Dataset.objects.count() == 3


@pytest.mark.django_db
def test_seed_uses_first_user_when_no_superuser(
    tmp_path: Path,
    make_user: Callable[..., User],
) -> None:
    """无超级用户时应回退到 User 表中第一个用户作为 Dataset owner."""
    db_file = tmp_path / "sample.db"
    _make_sample_db(db_file)

    DataSource.objects.create(
        name="sample_demo",
        engine=EngineType.SQLITE,
        database=str(db_file.resolve()),
    )
    # 创建一个普通 viewer 用户（非 superuser），作为回退 owner
    fallback = make_user(username="plain", role=Role.VIEWER)
    assert fallback.is_superuser is False

    call_command("seed_sample_data", stdout=StringIO())
    assert Dataset.objects.count() == 3
    owner_ids = set(Dataset.objects.values_list("owner_id", flat=True))
    assert owner_ids == {fallback.pk}
