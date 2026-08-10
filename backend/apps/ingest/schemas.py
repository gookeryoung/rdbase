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
    quality_score: float = 100.0


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
    avg_quality_score: float = 0.0


class IngestFieldHealthOut(BaseModel):
    """爬取字段健康度输出（P8-Q3）.

    按 (field, rule) 聚合历史质量报告，用于监控面板字段健康度展示。

    Attributes:
        field: 字段名。
        rule: 规则类型。
        avg_pass_rate: 最近 N 次平均通过率。
        total_checks: 最近 N 次累计检查次数。
        total_failures: 最近 N 次累计失败次数。
        last_pass_rate: 最近一次通过率（趋势指示）。
        last_report_at: 最近一次报告时间。
        samples: 参与统计的样本数。
    """

    field: str
    rule: str
    avg_pass_rate: float
    total_checks: int
    total_failures: int
    last_pass_rate: float
    last_report_at: datetime
    samples: int


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


class IngestQualityReportOut(BaseModel):
    """爬取数据质量报告输出.

    Attributes:
        id: 报告 ID。
        task_id: 爬取任务 ID。
        log_id: 关联的执行日志 ID。
        field: 字段名。
        rule: 规则类型（required/range/regex/enum/unique/expression）。
        total_count: 样本总数。
        passed_count: 通过数。
        failed_count: 失败数。
        pass_rate: 通过率（0-100，保留一位小数）。
        failure_samples: 失败样本数组（最多 20 条）。
        created_at: 报告创建时间。
    """

    id: int
    task_id: int
    log_id: int
    field: str
    rule: str
    total_count: int
    passed_count: int
    failed_count: int
    pass_rate: float
    failure_samples: list[Any]
    created_at: datetime


class IngestQualitySummaryOut(BaseModel):
    """爬取任务最近一批质量报告的汇总摘要.

    Attributes:
        task_id: 爬取任务 ID。
        total_rules: 规则数。
        avg_pass_rate: 平均通过率。
        worst_field: 通过率最低的字段名。
        worst_rule: 通过率最低的规则类型。
        total_failures: 失败样本总数。
        last_report_at: 最近一次报告时间，无报告时为 None。
    """

    task_id: int
    total_rules: int
    avg_pass_rate: float
    worst_field: str
    worst_rule: str
    total_failures: int
    last_report_at: datetime | None = None
