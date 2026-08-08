"""system Router - 系统运维接口.

仅管理员可访问。提供：

- GET /system/health：详细健康检查（DB/磁盘/Redis/连接池）
- GET /system/pool-stats：数据源连接池状态
- GET /system/circuit-states：熔断器状态
- GET /system/locks：分布式锁状态
"""

from __future__ import annotations

from dataclasses import asdict

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin

from .circuit_breaker import list_breakers
from .distributed_lock import list_lock_info
from .health import build_health
from .pool_monitor import collect_pool_stats
from .schemas import (
    CircuitStateOut,
    CircuitStatesOut,
    HealthOut,
    LockInfoOut,
    LockListOut,
    PoolStatOut,
    PoolStatsOut,
)

router = Router(tags=["system"], auth=JWTAuth())


@router.get("/health", response={200: HealthOut})
def health_view(request: HttpRequest) -> HttpResponse:
    """详细健康检查（仅管理员）.

    返回各组件状态、延迟与详情；与 ``/health/ready`` 共用底层检查器，
    但本接口始终返回 200（用于管理员查看当前状态，不作为就绪探针）。
    """
    require_admin(request)
    body = build_health()
    return JsonResponse(body)


@router.get("/pool-stats", response={200: PoolStatsOut})
def pool_stats_view(request: HttpRequest) -> HttpResponse:
    """数据源连接池状态（仅管理员）."""
    require_admin(request)
    stats = collect_pool_stats()
    items = [PoolStatOut(**asdict(s)).model_dump() for s in stats]
    body = PoolStatsOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.get("/circuit-states", response={200: CircuitStatesOut})
def circuit_states_view(request: HttpRequest) -> HttpResponse:
    """熔断器状态（仅管理员）.

    返回所有已注册熔断器的当前状态、失败计数、OPEN 剩余时长等，供运维监控。
    """
    require_admin(request)
    items = [CircuitStateOut(**b.snapshot()).model_dump() for b in list_breakers()]
    body = CircuitStatesOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.get("/locks", response={200: LockListOut})
def locks_view(request: HttpRequest) -> HttpResponse:
    """分布式锁状态（仅管理员）.

    返回当前后端中所有持有的锁名称、持有状态与剩余 TTL，供运维监控。
    """
    require_admin(request)
    infos = list_lock_info()
    items = [LockInfoOut(name=i.name, held=i.held, ttl=i.ttl).model_dump() for i in infos]
    body = LockListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


__all__ = ["router"]
