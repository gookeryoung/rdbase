"""连接池监控测试.

覆盖：状态文本解析、泄露检测、采集器（含异常路径）、管理员 API 权限与响应。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources import engine as ds_engine
from apps.system import pool_monitor
from apps.system.pool_monitor import (
    PoolStat,
    _detect_leak,
    _parse_status,
    collect_pool_stats,
)
from django.http import HttpResponse
from django.test import Client

_STATUS_TEXT = "Pool size: 5  Connections in pool: 2  Current Overflow: 0  Current Checked out connections: 1"


def test_parse_status_extracts_fields() -> None:
    """正常 QueuePool 文本应解析出四个字段."""
    parsed = _parse_status(_STATUS_TEXT)
    assert parsed == {
        "size": 5,
        "checked_in": 2,
        "checked_out": 1,
        "overflow": 0,
    }


def test_parse_status_returns_empty_on_mismatch() -> None:
    """非 QueuePool 文本应返回空字典."""
    assert _parse_status("SingletonThreadPool id=123") == {}
    assert _parse_status("") == {}


def test_detect_leak_below_threshold() -> None:
    """占用率低于阈值不告警."""
    alert, detail = _detect_leak({"size": 5, "checked_out": 1})
    assert alert is False
    assert detail == ""


def test_detect_leak_above_threshold() -> None:
    """占用率超过阈值标记告警."""
    alert, detail = _detect_leak({"size": 5, "checked_out": 5})
    assert alert is True
    assert "80%" in detail


def test_detect_leak_zero_size_not_alert() -> None:
    """size=0 时不告警（避免除零）."""
    alert, detail = _detect_leak({"size": 0, "checked_out": 0})
    assert alert is False
    assert detail == ""


def test_detect_leak_missing_fields_not_alert() -> None:
    """字段缺失时不告警."""
    alert, _ = _detect_leak({})
    assert alert is False


@pytest.mark.django_db
def test_collect_pool_stats_empty() -> None:
    """无缓存引擎时返回空列表."""
    assert collect_pool_stats() == []


@pytest.fixture
def _injected_engine() -> Any:
    """向 _engine_cache 注入伪引擎并在测试后清理."""
    fake_pool = SimpleNamespace(status=lambda: _STATUS_TEXT)
    fake_engine = SimpleNamespace(pool=fake_pool)
    ds_engine._engine_cache[999001] = fake_engine  # type: ignore[assignment]
    try:
        yield fake_engine
    finally:
        ds_engine._engine_cache.pop(999001, None)


@pytest.mark.django_db
def test_collect_pool_stats_with_engine(_injected_engine: Any) -> None:
    """注入引擎后应采集到状态快照."""
    stats = collect_pool_stats()
    assert len(stats) == 1
    stat = stats[0]
    assert isinstance(stat, PoolStat)
    assert stat.datasource_id == 999001
    assert stat.datasource_name is None  # 注入的 id 不在 DataSource 表中
    assert stat.pool_size == 5
    assert stat.checked_in == 2
    assert stat.checked_out == 1
    assert stat.overflow == 0
    assert stat.leak_alert is False


@pytest.fixture
def _injected_leak_engine() -> Any:
    """注入高占用率引擎（疑似泄露）."""
    leak_text = "Pool size: 5  Connections in pool: 0  Current Overflow: 0  Current Checked out connections: 5"
    fake_engine = SimpleNamespace(pool=SimpleNamespace(status=lambda: leak_text))
    ds_engine._engine_cache[999002] = fake_engine  # type: ignore[assignment]
    try:
        yield fake_engine
    finally:
        ds_engine._engine_cache.pop(999002, None)


@pytest.mark.django_db
def test_collect_pool_stats_detects_leak(_injected_leak_engine: Any) -> None:
    """高占用率引擎应标记 leak_alert."""
    stats = collect_pool_stats()
    assert len(stats) == 1
    assert stats[0].leak_alert is True
    assert "泄露" in stats[0].leak_detail


def test_build_stat_handles_pool_status_failure() -> None:
    """pool.status() 抛异常时应降级为空字段."""
    fake_engine = SimpleNamespace(pool=SimpleNamespace(status=lambda: (_ for _ in ()).throw(RuntimeError("disposed"))))
    stat = pool_monitor._build_stat(999003, fake_engine, None)
    assert stat.pool_size is None
    assert stat.checked_in is None
    assert stat.checked_out is None
    assert stat.overflow is None
    assert stat.leak_alert is False
    assert "读取池状态失败" in stat.leak_detail


# ---------- API 视图 ----------


def _auth_header(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.mark.django_db
def test_pool_stats_view_admin_ok(admin_user: User) -> None:
    """管理员访问 /api/v1/system/pool-stats 返回 200 与聚合结构."""
    client = Client(**_auth_header(admin_user))
    response = client.get("/api/v1/system/pool-stats")
    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "items" in body
    assert body["total"] == len(body["items"])


@pytest.mark.django_db
def test_pool_stats_view_forbidden_for_viewer(regular_user: User) -> None:
    """viewer 角色访问应返回 403."""
    client = Client(**_auth_header(regular_user))
    response = client.get("/api/v1/system/pool-stats")
    assert response.status_code == 403


@pytest.mark.django_db
def test_pool_stats_view_unauth_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = client.get("/api/v1/system/pool-stats")
    assert response.status_code == 401


@pytest.mark.django_db
def test_pool_stats_view_forbidden_for_designer(designer_user: User) -> None:
    """designer 角色访问应返回 403（仅管理员）."""
    client = Client(**_auth_header(designer_user))
    response = client.get("/api/v1/system/pool-stats")
    assert response.status_code == 403
