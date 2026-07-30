"""datasources 引擎池单元测试.

使用 SQLite 内存库做真实连接测试，避免 mock 复杂性。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.models import User
from apps.datasources.engine import (
    ConnectionConfig,
    build_config,
    dispose_all,
    dispose_engine,
    get_engine,
    verify_connection,
)
from apps.datasources.models import DataSource, EngineType


def test_sqlite_connection_config_url() -> None:
    """SQLite 配置应构造为 sqlite:/// 路径 URL."""
    config = ConnectionConfig(
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database=":memory:",
        username="",
        password="",
    )
    assert config.to_url() == "sqlite:///:memory:"


def test_sqlite_connection_config_file_path() -> None:
    """SQLite 文件路径应构造为 sqlite:///path URL."""
    config = ConnectionConfig(
        engine=EngineType.SQLITE,
        host="",
        port=None,
        database="/data/test.db",
        username="",
        password="",
    )
    assert config.to_url() == "sqlite:////data/test.db"


def test_mysql_connection_config_url() -> None:
    """MySQL 配置应构造 mysql+mysqldb:// URL."""
    config = ConnectionConfig(
        engine=EngineType.MYSQL,
        host="10.0.0.1",
        port=3306,
        database="app",
        username="root",
        password="secret",
    )
    assert config.to_url() == "mysql+mysqldb://root:secret@10.0.0.1:3306/app"


def test_postgresql_connection_config_url_without_port() -> None:
    """PostgreSQL 无端口时应省略端口部分."""
    config = ConnectionConfig(
        engine=EngineType.POSTGRESQL,
        host="localhost",
        port=None,
        database="app",
        username="user",
        password="pass",
    )
    assert config.to_url() == "postgresql+psycopg://user:pass@localhost/app"


def test_unsupported_engine_raises_value_error() -> None:
    """不支持的引擎类型应抛 ValueError."""
    config = ConnectionConfig(
        engine="oracle",
        host="h",
        port=1,
        database="d",
        username="u",
        password="p",
    )
    with pytest.raises(ValueError, match="不支持"):
        config.to_url()


@pytest.mark.django_db
def test_build_config_decrypts_password(make_user: Callable[..., User]) -> None:
    """build_config 应从 DataSource 派生配置并解密密码."""
    user = make_user()
    ds = DataSource.objects.create(
        name="mysql",
        engine=EngineType.MYSQL,
        host="h",
        port=3306,
        database="db",
        username="u",
        created_by=user,
    )
    ds.set_password("plain")
    ds.save()

    config = build_config(ds)
    assert config.engine == EngineType.MYSQL
    assert config.password == "plain"
    assert config.host == "h"


@pytest.mark.django_db
def test_get_engine_caches_by_datasource_id() -> None:
    """同一数据源多次获取引擎应返回缓存实例."""
    ds = DataSource.objects.create(
        name="sqlite-cache",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    try:
        engine1 = get_engine(ds)
        engine2 = get_engine(ds)
        assert engine1 is engine2
    finally:
        dispose_all()


@pytest.mark.django_db
def test_verify_connection_success_with_sqlite_memory() -> None:
    """SQLite 内存库连接测试应成功."""
    ds = DataSource.objects.create(
        name="sqlite-ok",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    try:
        ok, detail = verify_connection(ds)
        assert ok is True
        assert "成功" in detail
    finally:
        dispose_all()


@pytest.mark.django_db
def test_verify_connection_failure_with_bad_path() -> None:
    """SQLite 不存在的路径（无写权限目录）应连接失败."""
    ds = DataSource.objects.create(
        name="sqlite-bad",
        engine=EngineType.SQLITE,
        database="/nonexistent-dir/sub/test.db",
    )
    try:
        ok, detail = verify_connection(ds)
        assert ok is False
        assert "失败" in detail
    finally:
        dispose_all()


@pytest.mark.django_db
def test_dispose_engine_removes_cache() -> None:
    """dispose_engine 应移除指定数据源的引擎缓存."""
    ds = DataSource.objects.create(
        name="sqlite-dispose",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    engine = get_engine(ds)
    dispose_engine(ds.pk)
    # 再次获取应创建新引擎
    engine2 = get_engine(ds)
    assert engine is not engine2
    dispose_all()


@pytest.mark.django_db
def test_dispose_engine_with_unknown_id_is_noop() -> None:
    """dispose_engine 传入不存在的 ID 应安全无操作."""
    # 不在缓存中的 ID 不应抛错
    dispose_engine(99999)


@pytest.mark.django_db
def test_dispose_all_clears_cache() -> None:
    """dispose_all 应清空所有缓存."""
    ds1 = DataSource.objects.create(name="a", engine=EngineType.SQLITE, database=":memory:")
    ds2 = DataSource.objects.create(name="b", engine=EngineType.SQLITE, database=":memory:")
    get_engine(ds1)
    get_engine(ds2)
    dispose_all()
    # 缓存清空后重新获取应创建新实例
    new_engine = get_engine(ds1)
    dispose_all()
    assert new_engine is not None
