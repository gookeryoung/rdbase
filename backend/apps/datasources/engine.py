"""SQLAlchemy 引擎池.

按数据源动态创建 SQLAlchemy 2.x 引擎，缓存复用；提供连接测试与统一释放。
支持 MySQL/PostgreSQL/SQLite 三种方言，URL 由数据源字段构造。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .models import DataSource, EngineType


@dataclass(frozen=True)
class ConnectionConfig:
    """连接配置值对象，由 DataSource 派生，用于构造 SQLAlchemy URL."""

    engine: str
    host: str
    port: int | None
    database: str
    username: str
    password: str

    def to_url(self) -> str:
        """转换为 SQLAlchemy 连接 URL."""
        if self.engine == EngineType.SQLITE:
            # SQLite 使用文件路径或 :memory:
            return f"sqlite:///{self.database}"
        dialect = _DIALECT_MAP.get(self.engine)
        if dialect is None:
            raise ValueError(f"不支持的引擎类型: {self.engine}")
        port_part = f":{self.port}" if self.port else ""
        # 密码与用户名需 URL 编码，SQLAlchemy 会在 create_engine 时处理
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{dialect}://{auth}{self.host}{port_part}/{self.database}"


# 引擎类型到 SQLAlchemy dialect driver 的映射
_DIALECT_MAP: dict[str, str] = {
    EngineType.MYSQL: "mysql+mysqldb",
    EngineType.POSTGRESQL: "postgresql+psycopg",
    EngineType.SQLITE: "sqlite",
}


# 模块级引擎缓存：数据源 ID -> Engine，避免重复创建
_engine_cache: dict[int, Engine] = {}


def build_config(ds: DataSource) -> ConnectionConfig:
    """从 DataSource 模型实例派生 ConnectionConfig（解密密码）."""
    return ConnectionConfig(
        engine=cast(str, ds.engine),
        host=cast(str, ds.host),
        port=cast("int | None", ds.port),
        database=cast(str, ds.database),
        username=cast(str, ds.username),
        password=ds.get_password(),
    )


def get_engine(ds: DataSource) -> Engine:
    """获取或创建数据源对应的 SQLAlchemy 引擎（带缓存）."""
    if ds.pk in _engine_cache:
        return _engine_cache[ds.pk]
    config = build_config(ds)
    url = config.to_url()
    # SQLite 默认 check_same_thread=False 以支持多线程测试
    engine = (
        create_engine(url, pool_pre_ping=True, future=True)
        if ds.engine != EngineType.SQLITE
        else create_engine(url, future=True)
    )
    _engine_cache[ds.pk] = engine
    return engine


def verify_connection(ds: DataSource) -> tuple[bool, str]:
    """测试数据源连接，返回 (是否成功, 消息)."""
    try:
        engine = get_engine(ds)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "连接成功"
    except Exception as exc:  # 连接失败原因多样，统一捕获
        return False, f"连接失败: {exc}"


def dispose_engine(ds_id: int) -> None:
    """释放指定数据源的引擎缓存."""
    engine = _engine_cache.pop(ds_id, None)
    if engine is not None:
        engine.dispose()


def dispose_all() -> None:
    """释放全部引擎缓存（应用关闭时调用）."""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()


__all__ = [
    "ConnectionConfig",
    "build_config",
    "dispose_all",
    "dispose_engine",
    "get_engine",
    "verify_connection",
]
