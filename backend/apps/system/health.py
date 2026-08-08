"""深度健康检查.

提供两层探活：

- ``/health/live``：轻量存活探针，仅表明进程可响应（供负载均衡探活）。
- ``/health/ready``：就绪探针，跑数据库、磁盘、Redis、连接池四类检查器，
  任一 ``unhealthy`` 返回 503，``degraded`` 仍返回 200 但状态字段标记降级。
- ``/health/``：保留旧路径，返回与 ``/ready`` 相同的聚合结果以兼容。

检查器实现为独立函数，便于在管理员 API 与运维脚本中复用。
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeVar

from django.conf import settings
from django.db import connection
from django.db.utils import DatabaseError
from django.http import HttpRequest, JsonResponse

from .pool_monitor import collect_pool_stats
from .redis_client import ping_redis

logger = logging.getLogger(__name__)

# 磁盘可用空间阈值：低于 DEGRADED 标记降级，低于 UNHEALTHY 标记不可用
_DISK_DEGRADED_BYTES = 1024 * 1024 * 1024  # 1 GiB
_DISK_UNHEALTHY_BYTES = 100 * 1024 * 1024  # 100 MiB


class HealthStatus(str, Enum):
    """健康状态枚举（字符串值，便于 JSON 序列化）."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class ComponentStatus:
    """单个组件检查结果."""

    name: str
    status: HealthStatus
    latency_ms: int
    detail: str


T = TypeVar("T")


def _measure(fn: Callable[[], T]) -> tuple[int, T]:
    """测量函数执行耗时（毫秒），返回 (耗时, 返回值)."""
    start = time.perf_counter()
    result = fn()
    elapsed = int((time.perf_counter() - start) * 1000)
    return elapsed, result


def check_db() -> ComponentStatus:
    """检查平台数据库连通性（执行 SELECT 1）."""
    try:
        elapsed, _ = _measure(_ping_db)
    except DatabaseError as exc:
        logger.warning("数据库健康检查失败: %s", exc)
        return ComponentStatus(
            name="db",
            status=HealthStatus.UNHEALTHY,
            latency_ms=0,
            detail=f"数据库连接失败: {exc}",
        )
    return ComponentStatus(
        name="db",
        status=HealthStatus.HEALTHY,
        latency_ms=elapsed,
        detail="数据库连接正常",
    )


def _ping_db() -> None:
    """执行一次 SELECT 1 验证数据库连通性."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_disk() -> ComponentStatus:
    """检查数据目录所在磁盘可用空间."""
    data_dir = getattr(settings, "DATA_DIR", None)
    if data_dir is None:
        return ComponentStatus(
            name="disk",
            status=HealthStatus.DEGRADED,
            latency_ms=0,
            detail="未配置 DATA_DIR，跳过磁盘检查",
        )
    path = Path(str(data_dir))
    try:
        elapsed, free = _measure(lambda: _disk_free(path))
    except OSError as exc:
        logger.warning("磁盘健康检查失败: %s", exc)
        return ComponentStatus(
            name="disk",
            status=HealthStatus.UNHEALTHY,
            latency_ms=0,
            detail=f"读取磁盘空间失败: {exc}",
        )
    status, detail = _disk_status(free)
    return ComponentStatus(
        name="disk",
        status=status,
        latency_ms=elapsed,
        detail=detail,
    )


def _disk_free(path: Path) -> int:
    """返回 path 所在磁盘可用字节数；路径不存在时向上回溯到已存在父目录."""
    target = path
    while not target.exists():
        parent = target.parent
        if parent == target:
            break
        target = parent
    usage = shutil.disk_usage(str(target))
    return usage.free


def _disk_status(free_bytes: int) -> tuple[HealthStatus, str]:
    """按可用空间阈值判定磁盘状态."""
    free_mb = free_bytes / (1024 * 1024)
    if free_bytes < _DISK_UNHEALTHY_BYTES:
        return HealthStatus.UNHEALTHY, f"可用空间不足 100 MiB（当前 {free_mb:.1f} MiB）"
    if free_bytes < _DISK_DEGRADED_BYTES:
        return HealthStatus.DEGRADED, f"可用空间不足 1 GiB（当前 {free_mb:.1f} MiB）"
    return HealthStatus.HEALTHY, f"可用空间 {free_mb:.1f} MiB"


def check_redis() -> ComponentStatus:
    """检查 Redis 连通性；未配置时标记为降级."""
    elapsed, (ok, msg) = _measure(ping_redis)
    if ok:
        status = HealthStatus.HEALTHY
    else:
        # Redis 未配置视为降级（系统仍可工作，但缓存等能力缺失）
        status = HealthStatus.DEGRADED if "未配置" in msg else HealthStatus.UNHEALTHY
    return ComponentStatus(
        name="redis",
        status=status,
        latency_ms=elapsed,
        detail=msg,
    )


def check_pools() -> ComponentStatus:
    """检查数据源连接池占用率，存在疑似泄露时标记降级."""
    try:
        elapsed, stats = _measure(collect_pool_stats)
    except (RuntimeError, ValueError, KeyError) as exc:
        logger.warning("连接池健康检查失败: %s", exc)
        return ComponentStatus(
            name="pools",
            status=HealthStatus.DEGRADED,
            latency_ms=0,
            detail=f"采集连接池状态失败: {exc}",
        )
    if not stats:
        return ComponentStatus(
            name="pools",
            status=HealthStatus.HEALTHY,
            latency_ms=elapsed,
            detail="无活跃数据源引擎",
        )
    leaks = [s for s in stats if s.leak_alert]
    if leaks:
        names = [s.datasource_name or f"#{s.datasource_id}" for s in leaks]
        return ComponentStatus(
            name="pools",
            status=HealthStatus.DEGRADED,
            latency_ms=elapsed,
            detail=f"疑似泄露: {', '.join(names)}",
        )
    return ComponentStatus(
        name="pools",
        status=HealthStatus.HEALTHY,
        latency_ms=elapsed,
        detail=f"{len(stats)} 个引擎池状态正常",
    )


def _aggregate(components: list[ComponentStatus]) -> HealthStatus:
    """聚合各组件状态为整体状态."""
    if any(c.status == HealthStatus.UNHEALTHY for c in components):
        return HealthStatus.UNHEALTHY
    if any(c.status == HealthStatus.DEGRADED for c in components):
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def build_health() -> dict[str, object]:
    """运行全部检查器并返回聚合结果字典."""
    components = [check_db(), check_disk(), check_redis(), check_pools()]
    overall = _aggregate(components)
    return {
        "status": overall.value,
        "project": "rdbase",
        "components": [
            {
                "name": c.name,
                "status": c.status.value,
                "latency_ms": c.latency_ms,
                "detail": c.detail,
            }
            for c in components
        ],
    }


def live_view(_request: HttpRequest) -> JsonResponse:
    """轻量存活探针：仅表明进程可响应."""
    return JsonResponse({"status": "ok", "project": "rdbase"})


def ready_view(_request: HttpRequest) -> JsonResponse:
    """就绪探针：跑全部检查器，整体非健康返回 503."""
    body = build_health()
    status_code = 200 if body["status"] != HealthStatus.UNHEALTHY.value else 503
    return JsonResponse(body, status=status_code)


__all__ = [
    "ComponentStatus",
    "HealthStatus",
    "build_health",
    "check_db",
    "check_disk",
    "check_pools",
    "check_redis",
    "live_view",
    "ready_view",
]
