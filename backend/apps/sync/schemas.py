"""同步模块 Pydantic Schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SyncFieldMappingIn(BaseModel):
    """字段映射输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_field: str
    target_field: str
    mapping_type: str = "direct"
    fixed_value: str = ""
    is_pk: bool = False


class SyncFieldMappingOut(BaseModel):
    """字段映射输出."""

    id: int
    config_id: int
    source_field: str
    target_field: str
    mapping_type: str
    fixed_value: str
    is_pk: bool


class SyncConfigOut(BaseModel):
    """同步配置输出."""

    id: int
    name: str
    description: str
    source_table: str
    source_schema: str
    source_db_alias: str
    target_datasource_id: int
    target_table: str
    target_schema: str
    sync_mode: str
    status: str
    conflict_strategy: str = "upsert"
    timestamp_field: str
    batch_size: int
    scheduler_enabled: bool = False
    cron_expression: str = ""
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3
    created_by_id: int | None = None
    created_at: datetime
    updated_at: datetime
    last_sync_at: datetime | None = None
    field_mappings: list[SyncFieldMappingOut] = []


class SyncConfigListOut(BaseModel):
    """同步配置列表输出."""

    items: list[SyncConfigOut]
    total: int


class SyncConfigCreateIn(BaseModel):
    """创建同步配置输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    description: str = ""
    source_table: str
    source_schema: str = ""
    source_db_alias: str = "default"
    target_datasource_id: int
    target_table: str
    target_schema: str = ""
    sync_mode: str = "incremental"
    status: str = "active"
    conflict_strategy: str = "upsert"
    timestamp_field: str = "updated_at"
    batch_size: int = 500
    scheduler_enabled: bool = False
    cron_expression: str = ""
    max_retries: int = 3
    field_mappings: list[SyncFieldMappingIn] = []


class SyncConfigUpdateIn(BaseModel):
    """更新同步配置输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str | None = None
    target_table: str | None = None
    target_schema: str | None = None
    sync_mode: str | None = None
    status: str | None = None
    conflict_strategy: str | None = None
    timestamp_field: str | None = None
    batch_size: int | None = None
    scheduler_enabled: bool | None = None
    cron_expression: str | None = None
    max_retries: int | None = None
    field_mappings: list[SyncFieldMappingIn] | None = None


class SyncTriggerIn(BaseModel):
    """触发同步输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    confirm: bool = True
    force_full: bool = False


class SyncResultOut(BaseModel):
    """同步结果输出."""

    log_id: int
    status: str
    mode: str
    rows_read: int
    rows_written: int
    rows_skipped: int
    error_message: str
    duration_ms: int


class SyncLogOut(BaseModel):
    """同步日志输出."""

    id: int
    config_id: int
    status: str
    mode: str
    rows_read: int
    rows_written: int
    rows_skipped: int
    error_message: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int


class SyncLogListOut(BaseModel):
    """同步日志列表输出."""

    items: list[SyncLogOut]
    total: int


class SyncSourceTableOut(BaseModel):
    """源表信息输出."""

    table_name: str
    columns: list[dict[str, Any]]


class SyncTargetTableOut(BaseModel):
    """目标表信息输出."""

    table_name: str
    columns: list[dict[str, Any]]


class SyncPreviewOut(BaseModel):
    """同步预览输出."""

    config_id: int
    config_name: str
    mode: str
    total_rows: int
    sample_rows: list[dict[str, Any]] = []
    target_fields: list[str] = []
    pk_fields: list[str] = []
    can_sync: bool = True
    error_message: str = ""


class SyncBatchIn(BaseModel):
    """批量同步输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    config_ids: list[int]
    force_full: bool = False
    stop_on_error: bool = False
    max_workers: int = 1
    confirm: bool = True


class SyncBatchOut(BaseModel):
    """批量同步输出."""

    total: int
    succeeded: int
    failed: int
    skipped: int
    results: list[SyncResultOut] = []


class SyncScheduleIn(BaseModel):
    """调度配置输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scheduler_enabled: bool
    cron_expression: str = ""
    max_retries: int = 3


class MessageOut(BaseModel):
    """通用消息输出."""

    detail: str


class SyncStatsOut(BaseModel):
    """同步统计输出.

    汇总一段时间内（或某配置）的同步执行情况，用于监控面板展示。
    success_rate 为百分比（0-100，保留一位小数）。
    """

    total: int
    succeeded: int
    partial: int
    failed: int
    success_rate: float
    avg_duration_ms: int
    total_rows_read: int
    total_rows_written: int
    total_rows_skipped: int


class SyncAlertOut(BaseModel):
    """同步告警输出."""

    id: int
    config_id: int
    config_name: str
    level: str
    message: str
    acknowledged: bool
    acknowledged_at: datetime | None = None
    created_at: datetime


class SyncAlertListOut(BaseModel):
    """同步告警列表输出."""

    items: list[SyncAlertOut]
    total: int
    unacknowledged: int


__all__ = [
    "MessageOut",
    "SyncAlertListOut",
    "SyncAlertOut",
    "SyncBatchIn",
    "SyncBatchOut",
    "SyncConfigCreateIn",
    "SyncConfigListOut",
    "SyncConfigOut",
    "SyncConfigUpdateIn",
    "SyncFieldMappingIn",
    "SyncFieldMappingOut",
    "SyncLogListOut",
    "SyncLogOut",
    "SyncPreviewOut",
    "SyncResultOut",
    "SyncScheduleIn",
    "SyncSourceTableOut",
    "SyncStatsOut",
    "SyncTargetTableOut",
    "SyncTriggerIn",
]
