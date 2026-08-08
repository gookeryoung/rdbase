"""system 应用 Schema（Pydantic）."""

from __future__ import annotations

from datetime import datetime

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


class BackupFileInfoOut(Schema):
    """单个备份归档文件信息."""

    filename: str
    size: int
    modified_at: datetime


class BackupListOut(Schema):
    """备份归档列表响应."""

    items: list[BackupFileInfoOut]
    total: int


class BackupTaskOut(Schema):
    """备份任务状态响应."""

    id: int
    action: str
    status: str
    archive_name: str
    archive_size: int | None = None
    engine: str
    error_message: str
    created_at: datetime
    completed_at: datetime | None = None


class BackupTriggerOut(Schema):
    """备份/恢复触发响应."""

    task_id: int
    status: str
    message: str


class RestoreTriggerIn(Schema):
    """恢复触发请求体."""

    archive_name: str
    confirm: bool = False


class ChainBreakOut(Schema):
    """哈希链断点信息."""

    record_id: int
    expected_hash: str
    actual_hash: str
    prev_hash: str


class AuditVerifyOut(Schema):
    """审计哈希链校验结果."""

    valid: bool
    total_records: int
    breaks: list[ChainBreakOut]


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


__all__ = [
    "AuditVerifyOut",
    "BackupFileInfoOut",
    "BackupListOut",
    "BackupTaskOut",
    "BackupTriggerOut",
    "ChainBreakOut",
    "CircuitStateOut",
    "CircuitStatesOut",
    "ComponentStatusOut",
    "HealthOut",
    "LockInfoOut",
    "LockListOut",
    "MessageOut",
    "PoolStatOut",
    "PoolStatsOut",
    "RestoreTriggerIn",
]
