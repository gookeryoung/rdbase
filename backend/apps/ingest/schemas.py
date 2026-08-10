"""数据摄取模块 Pydantic Schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class MessageOut(BaseModel):
    """通用消息输出."""

    message: str


class IngestFieldMappingIn(BaseModel):
    """字段映射输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_field: str
    target_field: str
    mapping_type: str = "direct"
    fixed_value: str = ""
    is_pk: bool = False


class IngestFieldMappingOut(BaseModel):
    """字段映射输出."""

    id: int
    task_id: int
    source_field: str
    target_field: str
    mapping_type: str
    fixed_value: str
    is_pk: bool


class IngestTaskCreateIn(BaseModel):
    """爬取任务创建输入."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    description: str = ""
    source_type: str
    source_url: str
    parse_config: dict[str, Any] = {}
    request_config: dict[str, Any] = {}
    headers: dict[str, str] = {}
    auth_type: str = "none"
    target_datasource_id: int
    target_table: str
    conflict_strategy: str = "upsert"
    batch_size: int = 500
    obey_robots: bool = True
    scheduler_enabled: bool = False
    cron_expression: str = ""
    clean_config: dict[str, Any] = {}
    validation_config: dict[str, Any] = {}
    field_mappings: list[IngestFieldMappingIn] = []


class IngestTaskUpdateIn(BaseModel):
    """爬取任务更新输入（全部字段可选）."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = None
    description: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    parse_config: dict[str, Any] | None = None
    request_config: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    auth_type: str | None = None
    target_datasource_id: int | None = None
    target_table: str | None = None
    conflict_strategy: str | None = None
    batch_size: int | None = None
    obey_robots: bool | None = None
    scheduler_enabled: bool | None = None
    cron_expression: str | None = None
    status: str | None = None
    clean_config: dict[str, Any] | None = None
    validation_config: dict[str, Any] | None = None
    field_mappings: list[IngestFieldMappingIn] | None = None


class IngestTaskOut(BaseModel):
    """爬取任务输出."""

    id: int
    name: str
    description: str
    source_type: str
    source_url: str
    parse_config: dict[str, Any]
    request_config: dict[str, Any]
    has_headers: bool
    auth_type: str
    target_datasource_id: int
    target_table: str
    conflict_strategy: str
    batch_size: int
    obey_robots: bool
    scheduler_enabled: bool
    cron_expression: str
    clean_config: dict[str, Any]
    validation_config: dict[str, Any]
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_sync_at: datetime | None = None
    retry_count: int
    max_retries: int
    status: str
    created_by_id: int | None = None
    created_at: datetime
    updated_at: datetime
    field_mappings: list[IngestFieldMappingOut] = []


class IngestLogOut(BaseModel):
    """爬取日志输出."""

    id: int
    task_id: int
    status: str
    rows_read: int
    rows_written: int
    rows_skipped: int
    error_message: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int


class IngestAlertOut(BaseModel):
    """爬取告警输出."""

    id: int
    task_id: int
    level: str
    message: str
    acknowledged: bool
    acknowledged_at: datetime | None = None
    created_at: datetime


class IngestRunOut(BaseModel):
    """爬取执行结果输出."""

    task_id: int
    returncode: int
    log: IngestLogOut | None = None
    stderr: str = ""


class IngestStatsOut(BaseModel):
    """爬取统计输出."""

    total: int
    succeeded: int
    partial: int
    failed: int
    success_rate: float
    avg_duration_ms: int
    total_rows_read: int
    total_rows_written: int
    total_rows_skipped: int


class IngestTriggerOut(BaseModel):
    """外部触发爬取任务结果输出（与 IngestRunOut 同构，语义为外部触发执行结果）.

    Attributes:
        task_id: 爬取任务 ID。
        returncode: 子进程退出码（0 表示成功）。
        log: 最新一条执行日志；任务无日志时为 None。
        stderr: 子进程标准错误输出。
    """

    task_id: int
    returncode: int
    log: IngestLogOut | None = None
    stderr: str = ""
