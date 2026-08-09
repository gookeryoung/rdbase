"""填充示例业务表数据与数据集配置的管理命令.

向 ``sample_demo`` 数据源的 users / products / orders 表各插入 30 行测试数据，
并创建 3 个 Dataset 配置记录，便于 Datasets 管理页面预览与外部 API 调试::

    python manage.py seed_sample_data

幂等设计：

- 业务表用 ``INSERT OR IGNORE`` 按主键去重，已存在的 id 跳过；
- Dataset 按 ``slug`` 判断存在性，已存在则跳过创建。
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.datasources.models import Dataset, DataSource

# 每张业务表填充的行数
_ROWS_PER_TABLE = 30

# Dataset 配置：3 组示例，分别展示「全字段」「列级裁剪」「行级过滤」三种特性
_DATASET_CONFIGS: list[dict[str, Any]] = [
    {
        "slug": "user-profiles",
        "name": "用户档案",
        "description": "用户基本信息档案，展示列级裁剪：隐藏 email 列",
        "table_name": "users",
        "fields_whitelist": ["id", "name", "age"],
        "filter_expression": {},
        "aggregations": {},
    },
    {
        "slug": "product-catalog",
        "name": "商品目录",
        "description": "商品目录信息，展示行级过滤：仅返回 stock>0 的有库存商品",
        "table_name": "products",
        "fields_whitelist": ["id", "name", "price", "stock"],
        "filter_expression": {"stock": {"op": "gt", "val": 0}},
        "aggregations": {},
    },
    {
        "slug": "order-records",
        "name": "订单记录",
        "description": "订单交易记录，全字段开放、无行级过滤",
        "table_name": "orders",
        "fields_whitelist": ["id", "user_id", "product_id", "quantity", "created_at"],
        "filter_expression": {},
        "aggregations": {},
    },
]

# 姓名字符池（用于生成中文姓名）
_SURNAMES = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴"]
_GIVEN_NAMES = [
    "伟",
    "芳",
    "娜",
    "敏",
    "静",
    "丽",
    "强",
    "磊",
    "军",
    "洋",
    "勇",
    "艳",
    "杰",
    "娟",
    "涛",
    "明",
    "超",
    "霞",
    "平",
    "刚",
]

# 商品类目（用于生成商品名）
_CATEGORIES = ["手机", "笔记本", "平板", "耳机", "音箱", "键盘", "鼠标", "显示器", "路由器", "充电器"]


class Command(BaseCommand):
    """填充示例业务表数据与数据集配置."""

    help = "向 sample_demo 数据源填充 30 行测试数据并创建 3 个示例 Dataset"

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """执行数据填充与 Dataset 创建."""
        datasource = self._get_sample_datasource()
        db_path = self._resolve_db_path(datasource)
        self.stdout.write(f"目标数据源: {datasource.name} ({db_path})")

        # 固定随机种子保证可重复执行时数据稳定（id 已存在时 OR IGNORE 跳过）
        random.seed(42)

        conn = sqlite3.connect(str(db_path))
        try:
            u = self._seed_users(conn)
            p = self._seed_products(conn)
            o = self._seed_orders(conn)
        finally:
            conn.close()
        self.stdout.write(self.style.SUCCESS(f"业务表数据已就绪: users={u} products={p} orders={o}"))

        created, skipped = self._seed_datasets(datasource)
        self.stdout.write(self.style.SUCCESS(f"Dataset 配置: 新建 {created} 个，跳过 {skipped} 个"))

    # ------------------------------------------------------------
    # 数据源解析
    # ------------------------------------------------------------

    def _get_sample_datasource(self) -> DataSource:
        """获取 sample_demo 数据源；不存在则报错退出."""
        try:
            return DataSource.objects.get(name="sample_demo")
        except DataSource.DoesNotExist as exc:  # type: ignore[missing-attribute]
            raise SystemExit('数据源 "sample_demo" 不存在，请先运行 `python manage.py scan_datasources`') from exc

    def _resolve_db_path(self, datasource: DataSource) -> Path:
        """从数据源配置解析 SQLite 文件绝对路径."""
        path = Path(datasource.database)
        if not path.is_absolute():
            # 相对路径基于 BASE_DIR 解析
            base = Path(settings.BASE_DIR).parent  # backend/ 的上一级即项目根
            path = base / path
        if not path.exists():
            raise SystemExit(f"数据库文件不存在: {path}")
        return path

    # ------------------------------------------------------------
    # 业务表数据填充（INSERT OR IGNORE 幂等）
    # ------------------------------------------------------------

    def _seed_users(self, conn: sqlite3.Connection) -> int:
        """向 users 表插入 30 行测试数据，返回当前总行数."""
        cur = conn.cursor()
        for i in range(1, _ROWS_PER_TABLE + 1):
            name = random.choice(_SURNAMES) + random.choice(_GIVEN_NAMES)
            email = f"user{i}@example.com"
            age = random.randint(18, 60)
            cur.execute(
                "INSERT OR IGNORE INTO users (id, name, email, age) VALUES (?, ?, ?, ?)",
                (i, name, email, age),
            )
        conn.commit()
        return cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def _seed_products(self, conn: sqlite3.Connection) -> int:
        """向 products 表插入 30 行测试数据，返回当前总行数."""
        cur = conn.cursor()
        for i in range(1, _ROWS_PER_TABLE + 1):
            name = f"{random.choice(_CATEGORIES)}-{i:03d}"
            price = round(random.uniform(9.9, 999.0), 2)
            # 约 20% 商品库存为 0，便于观察 product-catalog 的行级过滤效果
            stock = 0 if i % 5 == 0 else random.randint(1, 100)
            cur.execute(
                "INSERT OR IGNORE INTO products (id, name, price, stock) VALUES (?, ?, ?, ?)",
                (i, name, price, stock),
            )
        conn.commit()
        return cur.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    def _seed_orders(self, conn: sqlite3.Connection) -> int:
        """向 orders 表插入 30 行测试数据，返回当前总行数."""
        cur = conn.cursor()
        base_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
        for i in range(1, _ROWS_PER_TABLE + 1):
            user_id = random.randint(1, _ROWS_PER_TABLE)
            product_id = random.randint(1, _ROWS_PER_TABLE)
            quantity = random.randint(1, 5)
            created_at = (base_time + timedelta(days=i, hours=random.randint(0, 23))).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT OR IGNORE INTO orders (id, user_id, product_id, quantity, created_at) VALUES (?, ?, ?, ?, ?)",
                (i, user_id, product_id, quantity, created_at),
            )
        conn.commit()
        return cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    # ------------------------------------------------------------
    # Dataset 配置创建（按 slug 幂等）
    # ------------------------------------------------------------

    def _seed_datasets(self, datasource: DataSource) -> tuple[int, int]:
        """创建 3 个 Dataset 配置，返回 (新建数, 跳过数)."""
        admin = User.objects.filter(is_superuser=True).first() or User.objects.first()
        created = 0
        skipped = 0
        for cfg in _DATASET_CONFIGS:
            _, was_created = Dataset.objects.get_or_create(
                slug=cfg["slug"],
                defaults={
                    "name": cfg["name"],
                    "description": cfg["description"],
                    "datasource": datasource,
                    "table_name": cfg["table_name"],
                    "schema_name": "",
                    "fields_whitelist": list(cfg["fields_whitelist"]),
                    "filter_expression": dict(cfg["filter_expression"]),
                    "aggregations": dict(cfg["aggregations"]),
                    "owner": admin,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"  新增 Dataset: {cfg['slug']} -> {cfg['table_name']}")
            else:
                skipped += 1
                self.stdout.write(f"  跳过 Dataset: {cfg['slug']}（已存在）")
        return created, skipped
