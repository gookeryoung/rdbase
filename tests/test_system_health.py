"""深度健康检查测试.

覆盖：各组件检查器（DB/磁盘/Redis/连接池）、状态聚合、live/ready 视图、
管理员 API 权限与响应、兼容路径。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.system import health
from apps.system.health import (
    HealthStatus,
    build_health,
    check_db,
    check_disk,
    check_pools,
    check_redis,
    live_view,
    ready_view,
)
from apps.system.redis_client import reset_redis_client
from django.db.utils import DatabaseError
from django.http import HttpRequest, JsonResponse
from django.test import Client, override_settings


@pytest.fixture(autouse=True)
def _reset_redis() -> Any:
    """每个测试前后清空 Redis 单例."""
    reset_redis_client()
    yield
    reset_redis_client()


# ---------- 组件检查器 ----------


@pytest.mark.django_db
def test_check_db_healthy() -> None:
    """正常数据库连接应返回 healthy."""
    status = check_db()
    assert status.name == "db"
    assert status.status == HealthStatus.HEALTHY
    assert status.latency_ms >= 0


def test_check_db_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库异常时应返回 unhealthy 并带错误信息."""

    def _raise() -> None:
        raise DatabaseError("connection lost")

    monkeypatch.setattr(health, "_ping_db", _raise)
    status = check_db()
    assert status.status == HealthStatus.UNHEALTHY
    assert "连接失败" in status.detail


@pytest.mark.django_db
def test_check_disk_healthy() -> None:
    """正常磁盘空间应返回 healthy."""
    status = check_disk()
    assert status.name == "disk"
    assert status.status == HealthStatus.HEALTHY
    assert "MiB" in status.detail


@pytest.mark.django_db
def test_check_disk_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """可用空间不足 1GiB 应标记 degraded."""

    def _fake_free(_path: Any) -> int:
        return 500 * 1024 * 1024

    monkeypatch.setattr(health, "_disk_free", _fake_free)
    status = check_disk()
    assert status.status == HealthStatus.DEGRADED


@pytest.mark.django_db
def test_check_disk_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """可用空间不足 100MiB 应标记 unhealthy."""

    def _fake_free(_path: Any) -> int:
        return 50 * 1024 * 1024

    monkeypatch.setattr(health, "_disk_free", _fake_free)
    status = check_disk()
    assert status.status == HealthStatus.UNHEALTHY


def test_check_redis_degraded_when_unconfigured() -> None:
    """未配置 Redis 时应标记 degraded."""
    with override_settings(REDIS_URL="", REDIS_FAKE=False):
        status = check_redis()
        assert status.name == "redis"
        assert status.status == HealthStatus.DEGRADED
        assert "未配置" in status.detail


def test_check_redis_healthy_with_fake() -> None:
    """fakeredis 下应返回 healthy."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        status = check_redis()
        assert status.status == HealthStatus.HEALTHY


@pytest.mark.django_db
def test_check_pools_no_engines() -> None:
    """无活跃引擎时应返回 healthy."""
    status = check_pools()
    assert status.name == "pools"
    assert status.status == HealthStatus.HEALTHY
    assert "无活跃" in status.detail


@pytest.mark.django_db
def test_check_pools_with_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """存在疑似泄露引擎时应标记 degraded."""
    from apps.system.pool_monitor import PoolStat

    fake_stat = PoolStat(
        datasource_id=1,
        datasource_name="leaky",
        status_text="",
        pool_size=5,
        checked_in=0,
        checked_out=5,
        overflow=0,
        leak_alert=True,
        leak_detail="疑似泄露",
    )
    monkeypatch.setattr(health, "collect_pool_stats", lambda: [fake_stat])
    status = check_pools()
    assert status.status == HealthStatus.DEGRADED
    assert "leaky" in status.detail


@pytest.mark.django_db
def test_check_pools_collect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """采集器抛异常时应标记 degraded."""

    def _raise() -> list[Any]:
        raise RuntimeError("engine cache corrupted")

    monkeypatch.setattr(health, "collect_pool_stats", _raise)
    status = check_pools()
    assert status.status == HealthStatus.DEGRADED
    assert "采集连接池状态失败" in status.detail


# ---------- 聚合 ----------


@pytest.mark.django_db
def test_build_health_aggregates_degraded() -> None:
    """默认配置（Redis 未配置）下整体应为 degraded."""
    body = build_health()
    assert body["status"] == HealthStatus.DEGRADED.value
    assert body["project"] == "rdbase"
    names = [c["name"] for c in body["components"]]  # type: ignore[union-attr]
    assert names == ["db", "disk", "redis", "pools"]


@pytest.mark.django_db
def test_build_health_unhealthy_when_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 不可用时整体应为 unhealthy."""

    def _raise() -> None:
        raise DatabaseError("down")

    monkeypatch.setattr(health, "_ping_db", _raise)
    body = build_health()
    assert body["status"] == HealthStatus.UNHEALTHY.value


@pytest.mark.django_db
def test_build_health_healthy_with_fake_redis() -> None:
    """全部组件健康时整体应为 healthy."""
    with override_settings(REDIS_FAKE=True, REDIS_URL=""):
        body = build_health()
        assert body["status"] == HealthStatus.HEALTHY.value


# ---------- 视图 ----------


def test_live_view_returns_ok() -> None:
    """live 探针应返回 200 与 ok."""
    request = HttpRequest()
    response = live_view(request)
    assert isinstance(response, JsonResponse)
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == "ok"


@pytest.mark.django_db
def test_ready_view_returns_200_when_degraded() -> None:
    """degraded 状态下 ready 探针应返回 200（仅 unhealthy 才 503）."""
    response = ready_view(HttpRequest())
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == HealthStatus.DEGRADED.value


@pytest.mark.django_db
def test_ready_view_returns_503_when_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """存在 unhealthy 组件时 ready 探针应返回 503."""

    def _raise() -> None:
        raise DatabaseError("down")

    monkeypatch.setattr(health, "_ping_db", _raise)
    response = ready_view(HttpRequest())
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body["status"] == HealthStatus.UNHEALTHY.value


@pytest.mark.django_db
def test_health_compat_path_returns_ready_body() -> None:
    """GET /health/ 应返回与 /health/ready 相同的聚合结构."""
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "components" in body
    assert body["project"] == "rdbase"


@pytest.mark.django_db
def test_health_ready_endpoint_returns_200() -> None:
    """GET /health/ready 在 degraded 时返回 200."""
    client = Client()
    response = client.get("/health/ready")
    assert response.status_code == 200


# ---------- 管理员 API ----------


def _auth_header(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.mark.django_db
def test_health_api_admin_ok(admin_user: User) -> None:
    """管理员访问 /api/v1/system/health 返回 200 与详细结构."""
    client = Client(**_auth_header(admin_user))
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["project"] == "rdbase"
    assert len(body["components"]) == 4


@pytest.mark.django_db
def test_health_api_forbidden_for_viewer(regular_user: User) -> None:
    """viewer 访问应返回 403."""
    client = Client(**_auth_header(regular_user))
    response = client.get("/api/v1/system/health")
    assert response.status_code == 403


@pytest.mark.django_db
def test_health_api_unauth_returns_401() -> None:
    """未认证访问应返回 401."""
    client = Client()
    response = client.get("/api/v1/system/health")
    assert response.status_code == 401


@pytest.mark.django_db
def test_health_api_forbidden_for_designer(designer_user: User) -> None:
    """designer 访问应返回 403."""
    client = Client(**_auth_header(designer_user))
    response = client.get("/api/v1/system/health")
    assert response.status_code == 403
