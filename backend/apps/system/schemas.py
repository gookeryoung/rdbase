"""system 应用 Schema（Pydantic）."""

from __future__ import annotations

from ninja import Schema


class ComponentStatusOut(Schema):
    """单个组件健康检查结果."""

    name: str
    status: str
    latency_ms: int
    detail: str


class HealthOut(Schema):
    """整体健康检查响应."""

    status: str
    project: str
    components: list[ComponentStatusOut]


class PoolStatOut(Schema):
    """单个数据源连接池状态."""

    datasource_id: int
    datasource_name: str | None
    status_text: str
    pool_size: int | None
    checked_in: int | None
    checked_out: int | None
    overflow: int | None
    leak_alert: bool
    leak_detail: str


class PoolStatsOut(Schema):
    """连接池状态聚合响应."""

    items: list[PoolStatOut]
    total: int


class CircuitStateOut(Schema):
    """单个熔断器状态快照."""

    name: str
    state: str
    failure_count: int
    failure_threshold: int
    opened_at: float
    open_seconds: float
    half_open_calls: int
    half_open_max_calls: int
    retry_after: float


class CircuitStatesOut(Schema):
    """熔断器状态聚合响应."""

    items: list[CircuitStateOut]
    total: int


class LockInfoOut(Schema):
    """单个分布式锁状态."""

    name: str
    held: bool
    ttl: int


class LockListOut(Schema):
    """分布式锁状态聚合响应."""

    items: list[LockInfoOut]
    total: int


__all__ = [
    "CircuitStateOut",
    "CircuitStatesOut",
    "ComponentStatusOut",
    "HealthOut",
    "LockInfoOut",
    "LockListOut",
    "PoolStatOut",
    "PoolStatsOut",
]
