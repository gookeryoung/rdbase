"""datasources 模型单元测试."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from django.db import IntegrityError


@pytest.mark.django_db
def test_create_mysql_datasource_with_encrypted_password(make_user: Callable[..., User]) -> None:
    """创建 MySQL 数据源应加密存储密码，且可解密回原文."""
    user = make_user(username="creator")
    ds = DataSource.objects.create(
        name="mysql-prod",
        engine=EngineType.MYSQL,
        host="10.0.0.1",
        port=3306,
        database="app",
        username="root",
        group="prod",
        tags=["primary", "online"],
        created_by=user,
    )
    ds.set_password("s3cret")
    ds.save()

    reloaded = DataSource.objects.get(pk=ds.pk)
    assert reloaded.password_encrypted != "s3cret"  # 密文不等于明文
    assert reloaded.get_password() == "s3cret"  # 解密还原
    assert reloaded.is_sqlite is False
    assert reloaded.tags == ["primary", "online"]


@pytest.mark.django_db
def test_create_sqlite_datasource_without_credentials() -> None:
    """SQLite 数据源无需 host/port/credentials，database 为文件路径."""
    ds = DataSource.objects.create(
        name="sqlite-local",
        engine=EngineType.SQLITE,
        database="/data/test.db",
    )
    assert ds.is_sqlite is True
    assert ds.host == ""
    assert ds.port is None
    assert ds.username == ""
    assert ds.get_password() == ""


@pytest.mark.django_db
def test_datasource_name_unique() -> None:
    """数据源名称唯一约束."""
    DataSource.objects.create(name="dup", engine=EngineType.SQLITE, database=":memory:")
    with pytest.raises(IntegrityError):
        DataSource.objects.create(name="dup", engine=EngineType.SQLITE, database=":memory:")


@pytest.mark.django_db
def test_datasource_default_group_and_tags() -> None:
    """未指定 group/tags 时使用默认值."""
    ds = DataSource.objects.create(
        name="defaulted",
        engine=EngineType.POSTGRESQL,
        host="localhost",
        port=5432,
        database="app",
    )
    assert ds.group == "default"
    assert ds.tags == []
    assert ds.is_active is True


@pytest.mark.django_db
def test_datasource_str_returns_name() -> None:
    """__str__ 应返回数据源名称."""
    ds = DataSource.objects.create(name="my-ds", engine=EngineType.SQLITE, database="x.db")
    assert str(ds) == "my-ds"


@pytest.mark.django_db
def test_set_password_encrypts_and_round_trips() -> None:
    """set_password 加密后 get_password 应解密还原."""
    ds = DataSource(name="ds", engine=EngineType.SQLITE, database="x.db")
    ds.set_password("abc")
    assert ds.password_encrypted
    assert ds.password_encrypted != "abc"
    assert ds.get_password() == "abc"


@pytest.mark.django_db
def test_datasource_registered_in_admin() -> None:
    """DataSource 应注册到 admin.site."""
    from django.contrib import admin

    assert DataSource in admin.site._registry
