"""datasources 引擎池单元测试.

使用 SQLite 内存库做真实连接测试，避免 mock 复杂性。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from apps.accounts.models import User
from apps.datasources.engine import (
    ConnectionConfig,
    _engine_cache,
    build_config,
    dispose_all,
    dispose_engine,
    get_engine,
    verify_connection,
)
from apps.datasources.models import DataSource, EngineType
from sqlalchemy import text
from sqlalchemy.engine import Engine


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


# ================================================================
# 并发安全测试（R1/R2/R3）
# ================================================================


@pytest.mark.django_db
def test_concurrent_get_engine_returns_single_instance() -> None:
    """多线程首次并发获取同一数据源应只创建一个引擎实例（R1）.

    验证双重检查锁定：并发 check-then-act 不会重复建池导致连接泄漏。
    以行为断言收口——所有线程拿到同一个 Engine，且缓存中只有一个条目。
    """
    ds = DataSource.objects.create(
        name="sqlite-concurrent",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    try:
        engines: list[Engine] = []
        engines_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker() -> None:
            """同时抢跑，最大化首次并发获取的竞态窗口."""
            barrier.wait()
            engine = get_engine(ds)
            with engines_lock:
                engines.append(engine)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(engines) == 8
        first = engines[0]
        assert all(engine is first for engine in engines)
        # 该数据源在缓存中只应有一个条目
        assert _engine_cache[ds.pk] is first
    finally:
        dispose_all()


@pytest.mark.django_db
def test_concurrent_write_file_sqlite_no_thread_error(tmp_path: Path) -> None:
    """文件型 SQLite 跨线程并发写入不应因 check_same_thread 报错（R3）.

    显式配置 check_same_thread=False 后，QueuePool 为每个线程分配独立连接，
    多线程写入均可成功；此处以真实写入验证不抛线程检查异常。
    """
    db_file = tmp_path / "concurrent.db"
    ds = DataSource.objects.create(
        name="sqlite-file-concurrent",
        engine=EngineType.SQLITE,
        database=str(db_file),
    )
    try:
        engine = get_engine(ds)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))

        errors: list[Exception] = []
        errors_lock = threading.Lock()
        barrier = threading.Barrier(4)

        def writer(offset: int) -> None:
            """每个线程写入一段不重叠的主键区间."""
            barrier.wait()
            try:
                for i in range(offset, offset + 50):
                    with engine.begin() as conn:
                        conn.execute(text("INSERT INTO t (id, v) VALUES (:id, :v)"), {"id": i, "v": i})
            except Exception as exc:  # 记录任何线程内异常供主线程断言
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n * 50,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM t")).scalar()
        assert count == 200
    finally:
        dispose_all()


@pytest.mark.django_db
def test_concurrent_get_and_dispose_is_stable() -> None:
    """get_engine 与 dispose_engine 并发交错不应抛异常（R2）.

    dispose 在锁内 pop、锁外 dispose，get 用双重检查锁定，二者交错时
    始终返回有效 Engine，不会出现 "dictionary changed size during iteration"
    或对已释放引擎的误用。
    """
    ds = DataSource.objects.create(
        name="sqlite-get-dispose",
        engine=EngineType.SQLITE,
        database=":memory:",
    )
    try:
        errors: list[Exception] = []
        errors_lock = threading.Lock()
        stop = threading.Event()

        def getter() -> None:
            """持续获取引擎，任何异常都记录."""
            while not stop.is_set():
                try:
                    engine = get_engine(ds)
                    assert engine is not None
                except Exception as exc:
                    with errors_lock:
                        errors.append(exc)
                    return

        def disposer() -> None:
            """持续释放引擎缓存，与 getter 交错."""
            while not stop.is_set():
                try:
                    dispose_engine(ds.pk)
                except Exception as exc:
                    with errors_lock:
                        errors.append(exc)
                    return

        threads = [threading.Thread(target=getter) for _ in range(3)]
        threads += [threading.Thread(target=disposer) for _ in range(3)]
        for t in threads:
            t.start()
        stop_timer = threading.Timer(0.3, stop.set)
        stop_timer.start()
        for t in threads:
            t.join()
        stop_timer.cancel()

        assert errors == []
    finally:
        dispose_all()
