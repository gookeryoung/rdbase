"""数据摄取模型.

定义从外部源（REST/JSON API、网页 HTML、文件下载、RSS/Atom）爬取数据并写入
已配置数据源的配置与日志。核心模型：
- IngestTask：爬取任务配置（源类型/URL/解析配置/请求配置/目标数据源/调度）
- IngestFieldMapping：字段映射（源字段 → 目标字段，支持直接/常量）
- IngestLog：爬取执行日志（每次执行的统计与状态）
- IngestAlert：爬取告警记录（失败达最大重试时产生）

敏感请求头（含 API Key/Cookie 等）经 Fernet 加密存入 headers_encrypted。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.datasources.crypto import InvalidToken, decrypt_password, encrypt_password
from apps.datasources.models import DataSource


class SourceType(models.TextChoices):
    """爬取源类型枚举."""

    API = "api", "REST/JSON API"
    HTML = "html", "网页 HTML"
    FILE = "file", "文件下载"
    RSS = "rss", "RSS/Atom"


class IngestStatus(models.TextChoices):
    """爬取任务状态枚举."""

    ACTIVE = "active", "启用"
    PAUSED = "paused", "暂停"
    ERROR = "error", "错误"


class IngestLogStatus(models.TextChoices):
    """爬取日志状态."""

    SUCCESS = "success", "成功"
    PARTIAL = "partial", "部分成功"
    FAILED = "failed", "失败"


class ConflictStrategy(models.TextChoices):
    """主键冲突处理策略枚举.

    写入目标表时遇到主键/唯一键冲突的处理方式，语义与 sync 模块一致：
    - UPSERT：存在则更新（INSERT ... ON CONFLICT DO UPDATE，默认）。
    - SKIP：存在则跳过（INSERT ... ON CONFLICT DO NOTHING）。
    - ERROR：存在则报错（普通 INSERT，冲突触发异常使整批失败）。
    """

    UPSERT = "upsert", "冲突则更新"
    SKIP = "skip", "冲突则跳过"
    ERROR = "error", "冲突则报错"


class AuthType(models.TextChoices):
    """鉴权类型枚举."""

    NONE = "none", "无鉴权"
    API_KEY = "api_key", "API Key"
    BEARER = "bearer", "Bearer Token"
    BASIC = "basic", "Basic Auth"
    CUSTOM = "custom", "自定义"


class AlertLevel(models.TextChoices):
    """爬取告警级别枚举."""

    WARNING = "warning", "警告"
    ERROR = "error", "错误"


@dataclass(frozen=True)
class IngestStats:
    """爬取执行统计聚合结果.

    汇总一段时间内（或某任务）的爬取日志，用于监控面板展示。
    success_rate 为百分比（0-100，保留一位小数），无日志时为 0。
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


class IngestTask(models.Model):
    """爬取任务配置.

    定义从某外部源爬取数据并写入指定数据源的目标表。支持 4 类源（API/HTML/FILE/RSS），
    经字段映射后按冲突策略写入。支持手动触发与 cron 定时调度。
    """

    objects: models.Manager[IngestTask]

    name = models.CharField(max_length=128, unique=True, verbose_name="任务名称")
    description = models.CharField(max_length=255, blank=True, default="", verbose_name="描述")

    # 源配置
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        verbose_name="源类型",
    )
    source_url = models.URLField(max_length=1024, verbose_name="源 URL")
    # 解析配置：CSS/XPath 选择器、JSONPath、文件格式、条目定位等（非敏感）
    parse_config = models.JSONField(default=dict, blank=True, verbose_name="解析配置")
    # 请求配置：HTTP method、body、分页规则、超时、重试等结构化配置（非敏感）
    request_config = models.JSONField(default=dict, blank=True, verbose_name="请求配置")
    # 敏感请求头（含 API Key/Cookie 等）整体 JSON 加密存储
    headers_encrypted = models.TextField(blank=True, default="", verbose_name="加密请求头")
    auth_type = models.CharField(
        max_length=20,
        choices=AuthType.choices,
        default=AuthType.NONE,
        verbose_name="鉴权类型",
    )

    # 目标写入
    target_datasource = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="ingest_tasks",
        verbose_name="目标数据源",
    )
    target_table = models.CharField(max_length=128, verbose_name="目标表名")
    conflict_strategy = models.CharField(
        max_length=20,
        choices=ConflictStrategy.choices,
        default=ConflictStrategy.UPSERT,
        verbose_name="主键冲突处理策略",
    )
    batch_size = models.PositiveIntegerField(default=500, verbose_name="批量大小")

    # 合规
    obey_robots = models.BooleanField(default=True, verbose_name="遵守 robots.txt")

    # 定时调度
    scheduler_enabled = models.BooleanField(default=False, verbose_name="启用定时调度")
    cron_expression = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Cron 表达式（如 */5 * * * * 表示每5分钟）",
    )
    last_run_at = models.DateTimeField(null=True, blank=True, verbose_name="上次执行时间")
    next_run_at = models.DateTimeField(null=True, blank=True, verbose_name="下次执行时间")
    retry_count = models.PositiveSmallIntegerField(default=0, verbose_name="已重试次数")
    max_retries = models.PositiveSmallIntegerField(default=3, verbose_name="最大重试次数")

    status = models.CharField(
        max_length=20,
        choices=IngestStatus.choices,
        default=IngestStatus.ACTIVE,
        verbose_name="状态",
    )

    # 最近一次爬取时间（用于增量去重基准）
    last_sync_at = models.DateTimeField(null=True, blank=True, verbose_name="最近爬取时间")

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ingest_tasks",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "爬取任务"
        verbose_name_plural = "爬取任务"
        ordering = ["-id"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return self.name  # type: ignore[bad-return]

    @property
    def is_active(self) -> bool:
        """是否启用."""
        return self.status == IngestStatus.ACTIVE

    @property
    def is_schedulable(self) -> bool:
        """是否可调度（启用且有 cron 表达式）."""
        return bool(self.is_active and self.scheduler_enabled and self.cron_expression)

    def set_headers(self, headers: dict[str, str]) -> None:
        """加密并保存请求头字典.

        Args:
            headers: 请求头键值对（可能含 API Key/Cookie 等敏感值）。
        """
        if not headers:
            self.headers_encrypted = ""
            return
        raw = json.dumps(headers, ensure_ascii=False, sort_keys=True)
        self.headers_encrypted = encrypt_password(raw, _get_secret_key())  # type: ignore[bad-assignment]

    def get_headers(self) -> dict[str, str]:
        """解密并返回请求头字典；未设置或密文损坏时返回空字典."""
        if not self.headers_encrypted:
            return {}
        try:
            raw = decrypt_password(self.headers_encrypted, _get_secret_key())
        except InvalidToken:
            return {}
        try:
            headers = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(headers, dict):
            return {}
        return {str(k): str(v) for k, v in headers.items()}

    def refresh_next_run(self, *, base: datetime | None = None, save: bool = True) -> datetime | None:
        """依据 cron 表达式刷新 next_run_at.

        可调度时基于 cron 计算下次执行时间；不可调度（未启用/无 cron/已暂停）
        则清空 next_run_at。计算失败（cron 非法）同样清空，避免残留过期时间。

        Args:
            base: 计算基准时间，None 则使用当前时区感知时间。
            save: 是否立即持久化 next_run_at 字段。

        Returns:
            datetime | None: 刷新后的下次执行时间，不可调度时为 None。
        """
        from apps.sync.scheduling import CronError, compute_next_run

        if self.is_schedulable:
            try:
                self.next_run_at = compute_next_run(self.cron_expression, base=base)
            except CronError:
                self.next_run_at = None
        else:
            self.next_run_at = None

        if save:
            self.save(update_fields=["next_run_at"])
        return self.next_run_at  # type: ignore[bad-return]


class IngestFieldMapping(models.Model):
    """爬取字段映射.

    定义源数据字段到目标表字段的映射关系。支持：
    - direct：直接映射（源字段值直接写入目标字段）
    - constant：常量写入（忽略源字段，使用 fixed_value）
    """

    objects: models.Manager[IngestFieldMapping]

    task = models.ForeignKey(
        IngestTask,
        on_delete=models.CASCADE,
        related_name="field_mappings",
        verbose_name="爬取任务",
    )
    source_field = models.CharField(max_length=128, verbose_name="源字段名")
    target_field = models.CharField(max_length=128, verbose_name="目标字段名")
    mapping_type = models.CharField(
        max_length=20,
        choices=[
            ("direct", "直接映射"),
            ("constant", "常量"),
        ],
        default="direct",
        verbose_name="映射类型",
    )
    fixed_value = models.CharField(
        max_length=255, blank=True, default="", verbose_name="常量值（mapping_type=constant 时使用）"
    )
    is_pk = models.BooleanField(default=False, verbose_name="是否主键")

    class Meta:
        verbose_name = "爬取字段映射"
        verbose_name_plural = "爬取字段映射"
        ordering = ["id"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.task.name}: {self.source_field} → {self.target_field}"  # type: ignore[bad-return]


class IngestLog(models.Model):
    """爬取执行日志.

    每次爬取执行产生一条日志记录，包含统计信息与状态。
    """

    objects: models.Manager[IngestLog]

    task = models.ForeignKey(
        IngestTask,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="爬取任务",
    )
    status = models.CharField(
        max_length=20,
        choices=IngestLogStatus.choices,
        verbose_name="执行状态",
    )
    rows_read = models.PositiveIntegerField(default=0, verbose_name="读取行数")
    rows_written = models.PositiveIntegerField(default=0, verbose_name="写入行数")
    rows_skipped = models.PositiveIntegerField(default=0, verbose_name="跳过行数")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    started_at = models.DateTimeField(verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration_ms = models.PositiveIntegerField(default=0, verbose_name="耗时（毫秒）")

    class Meta:
        verbose_name = "爬取日志"
        verbose_name_plural = "爬取日志"
        ordering = ["-started_at"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.task.name} {self.started_at}: {self.status} ({self.rows_written}行)"  # type: ignore[bad-return]

    @classmethod
    def aggregate_stats(cls, *, task_id: int | None = None, days: int | None = None) -> IngestStats:
        """聚合爬取日志，计算成功率、平均耗时与总读写行数.

        Args:
            task_id: 仅统计指定任务，None 则统计全部。
            days: 仅统计最近 days 天（按 started_at），None 或非正数则不限时间。

        Returns:
            IngestStats: 聚合统计结果，无匹配日志时各项为 0。
        """
        qs = cls.objects.all()
        if task_id is not None:
            qs = qs.filter(task_id=task_id)
        if days is not None and days > 0:
            qs = qs.filter(started_at__gte=timezone.now() - timedelta(days=days))

        agg = qs.aggregate(
            total=Count("id"),
            succeeded=Count("id", filter=Q(status=IngestLogStatus.SUCCESS)),
            partial=Count("id", filter=Q(status=IngestLogStatus.PARTIAL)),
            failed=Count("id", filter=Q(status=IngestLogStatus.FAILED)),
            avg_duration=Avg("duration_ms"),
            rows_read=Sum("rows_read"),
            rows_written=Sum("rows_written"),
            rows_skipped=Sum("rows_skipped"),
        )

        total = agg["total"] or 0
        succeeded = agg["succeeded"] or 0
        success_rate = round(succeeded / total * 100, 1) if total else 0.0

        return IngestStats(
            total=total,
            succeeded=succeeded,
            partial=agg["partial"] or 0,
            failed=agg["failed"] or 0,
            success_rate=success_rate,
            avg_duration_ms=int(agg["avg_duration"] or 0),
            total_rows_read=agg["rows_read"] or 0,
            total_rows_written=agg["rows_written"] or 0,
            total_rows_skipped=agg["rows_skipped"] or 0,
        )


class IngestAlert(models.Model):
    """爬取告警记录.

    爬取失败（达最大重试仍失败）时产生告警，供监控面板展示与确认处理。
    """

    objects: models.Manager[IngestAlert]

    task = models.ForeignKey(
        IngestTask,
        on_delete=models.CASCADE,
        related_name="alerts",
        verbose_name="爬取任务",
    )
    level = models.CharField(
        max_length=20,
        choices=AlertLevel.choices,
        default=AlertLevel.ERROR,
        verbose_name="告警级别",
    )
    message = models.TextField(verbose_name="告警内容")
    acknowledged = models.BooleanField(default=False, verbose_name="是否已确认")
    acknowledged_at = models.DateTimeField(null=True, blank=True, verbose_name="确认时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "爬取告警"
        verbose_name_plural = "爬取告警"
        ordering = ["-created_at"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"[{self.level}] {self.task.name}: {self.message[:40]}"  # type: ignore[bad-return]

    @classmethod
    def raise_alert(cls, task: IngestTask, message: str, *, level: str = AlertLevel.ERROR) -> IngestAlert:
        """为指定任务创建一条告警记录.

        Args:
            task: 触发告警的爬取任务。
            message: 告警内容（通常为失败原因）。
            level: 告警级别，默认 ERROR。

        Returns:
            IngestAlert: 新建的告警记录。
        """
        return cls.objects.create(task=task, level=level, message=message)

    def acknowledge(self, *, save: bool = True) -> None:
        """确认告警（标记已处理并记录确认时间）."""
        self.acknowledged = True
        self.acknowledged_at = timezone.now()
        if save:
            self.save(update_fields=["acknowledged", "acknowledged_at"])


def _get_secret_key() -> str:
    """读取 Django SECRET_KEY 作为加密密钥源."""
    key = getattr(settings, "SECRET_KEY", None)
    if not key:  # pragma: no cover - Django settings 必有 SECRET_KEY，不可达分支
        raise RuntimeError("SECRET_KEY 未配置，无法加解密爬取请求头")
    return key


__all__ = [
    "AlertLevel",
    "AuthType",
    "ConflictStrategy",
    "IngestAlert",
    "IngestFieldMapping",
    "IngestLog",
    "IngestLogStatus",
    "IngestStats",
    "IngestStatus",
    "IngestTask",
    "SourceType",
]
