"""数据同步模型.

定义从 rdbase 平台库（源）向外部数据源（目标）推送数据的配置与日志。
核心模型：
- SyncConfig：同步任务配置（源表 → 目标数据源/表，同步模式）
- SyncFieldMapping：字段映射（源字段 → 目标字段，支持表达式/常量）
- SyncLog：同步执行日志（每次执行的统计与状态）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import models
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.datasources.models import DataSource


class SyncMode(models.TextChoices):
    """同步模式枚举."""

    FULL = "full", "全量"
    INCREMENTAL = "incremental", "增量"


class SyncStatus(models.TextChoices):
    """同步任务状态枚举."""

    ACTIVE = "active", "启用"
    PAUSED = "paused", "暂停"
    ERROR = "error", "错误"


class SyncLogStatus(models.TextChoices):
    """同步日志状态."""

    SUCCESS = "success", "成功"
    PARTIAL = "partial", "部分成功"
    FAILED = "failed", "失败"


class ConflictStrategy(models.TextChoices):
    """主键冲突处理策略枚举.

    定义写入目标表时遇到主键/唯一键冲突的处理方式：
    - UPSERT：存在则更新（INSERT ... ON CONFLICT DO UPDATE，默认行为）。
    - SKIP：存在则跳过（INSERT ... ON CONFLICT DO NOTHING，保留目标已有值）。
    - ERROR：存在则报错（普通 INSERT，冲突触发异常并使整批失败）。
    """

    UPSERT = "upsert", "冲突则更新"
    SKIP = "skip", "冲突则跳过"
    ERROR = "error", "冲突则报错"


class AlertLevel(models.TextChoices):
    """同步告警级别枚举."""

    WARNING = "warning", "警告"
    ERROR = "error", "错误"


@dataclass(frozen=True)
class SyncStats:
    """同步执行统计聚合结果.

    汇总一段时间内（或某配置）的同步日志，用于监控面板展示。
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


class SyncConfig(models.Model):
    """同步任务配置.

    定义从 rdbase 平台库的某张表，向指定外部数据源的某张表推送数据。
    支持全量（每次推送全部数据）和增量（按 updated_at 字段筛选变更行）两种模式。
    """

    objects: models.Manager[SyncConfig]

    name = models.CharField(max_length=128, unique=True, verbose_name="配置名称")
    description = models.CharField(max_length=255, blank=True, default="", verbose_name="描述")

    # 源：rdbase 平台库的表（Django managed 应用中的表名）
    source_table = models.CharField(max_length=128, verbose_name="源表名")
    source_schema = models.CharField(max_length=128, blank=True, default="", verbose_name="源 Schema")
    source_db_alias = models.CharField(
        max_length=64,
        blank=True,
        default="default",
        verbose_name="源数据库别名（Django DATABASES key）",
    )

    # 目标：外部数据源
    target_datasource = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="sync_configs",
        verbose_name="目标数据源",
    )
    target_table = models.CharField(max_length=128, verbose_name="目标表名")
    target_schema = models.CharField(max_length=128, blank=True, default="", verbose_name="目标 Schema")

    # 同步模式与策略
    sync_mode = models.CharField(
        max_length=20,
        choices=SyncMode.choices,
        default=SyncMode.INCREMENTAL,
        verbose_name="同步模式",
    )
    status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.ACTIVE,
        verbose_name="状态",
    )
    conflict_strategy = models.CharField(
        max_length=20,
        choices=ConflictStrategy.choices,
        default=ConflictStrategy.UPSERT,
        verbose_name="主键冲突处理策略",
    )

    # 增量同步的时间戳字段（源表中用于判断变更的时间列）
    timestamp_field = models.CharField(
        max_length=64,
        blank=True,
        default="updated_at",
        verbose_name="增量时间戳字段",
    )

    # 批量大小
    batch_size = models.PositiveIntegerField(default=500, verbose_name="批量大小")

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

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_configs",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    # 最近一次同步时间（用于增量同步的起始点）
    last_sync_at = models.DateTimeField(null=True, blank=True, verbose_name="最近同步时间")

    class Meta:
        verbose_name = "同步配置"
        verbose_name_plural = "同步配置"
        ordering = ["-id"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return self.name  # type: ignore[bad-return]

    @property
    def is_active(self) -> bool:
        """是否启用."""
        return self.status == SyncStatus.ACTIVE

    @property
    def is_schedulable(self) -> bool:
        """是否可调度（启用且有 cron 表达式）."""
        return bool(self.is_active and self.scheduler_enabled and self.cron_expression)

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
        from .scheduling import CronError, compute_next_run

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


class SyncFieldMapping(models.Model):
    """同步字段映射.

    定义源表字段到目标表字段的映射关系。支持：
    - direct：直接映射（源字段值直接写入目标字段）
    - constant：常量写入（忽略源字段，使用 fixed_value）
    - expression：表达式（预留，后续扩展）
    """

    objects: models.Manager[SyncFieldMapping]

    config = models.ForeignKey(
        SyncConfig,
        on_delete=models.CASCADE,
        related_name="field_mappings",
        verbose_name="同步配置",
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
        verbose_name = "同步字段映射"
        verbose_name_plural = "同步字段映射"
        ordering = ["id"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.config.name}: {self.source_field} → {self.target_field}"  # type: ignore[bad-return]


class SyncLog(models.Model):
    """同步执行日志.

    每次同步执行产生一条日志记录，包含统计信息与状态。
    """

    objects: models.Manager[SyncLog]

    config = models.ForeignKey(
        SyncConfig,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="同步配置",
    )
    status = models.CharField(
        max_length=20,
        choices=SyncLogStatus.choices,
        verbose_name="执行状态",
    )
    mode = models.CharField(
        max_length=20,
        choices=SyncMode.choices,
        verbose_name="本次同步模式",
    )
    rows_read = models.PositiveIntegerField(default=0, verbose_name="读取行数")
    rows_written = models.PositiveIntegerField(default=0, verbose_name="写入行数")
    rows_skipped = models.PositiveIntegerField(default=0, verbose_name="跳过行数")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    started_at = models.DateTimeField(verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration_ms = models.PositiveIntegerField(default=0, verbose_name="耗时（毫秒）")

    class Meta:
        verbose_name = "同步日志"
        verbose_name_plural = "同步日志"
        ordering = ["-started_at"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"{self.config.name} {self.started_at}: {self.status} ({self.rows_written}行)"  # type: ignore[bad-return]

    @classmethod
    def aggregate_stats(cls, *, config_id: int | None = None, days: int | None = None) -> SyncStats:
        """聚合同步日志，计算成功率、平均耗时与总读写行数.

        Args:
            config_id: 仅统计指定配置，None 则统计全部。
            days: 仅统计最近 days 天（按 started_at），None 或非正数则不限时间。

        Returns:
            SyncStats: 聚合统计结果，无匹配日志时各项为 0。
        """
        qs = cls.objects.all()
        if config_id is not None:
            qs = qs.filter(config_id=config_id)
        if days is not None and days > 0:
            qs = qs.filter(started_at__gte=timezone.now() - timedelta(days=days))

        agg = qs.aggregate(
            total=Count("id"),
            succeeded=Count("id", filter=Q(status=SyncLogStatus.SUCCESS)),
            partial=Count("id", filter=Q(status=SyncLogStatus.PARTIAL)),
            failed=Count("id", filter=Q(status=SyncLogStatus.FAILED)),
            avg_duration=Avg("duration_ms"),
            rows_read=Sum("rows_read"),
            rows_written=Sum("rows_written"),
            rows_skipped=Sum("rows_skipped"),
        )

        total = agg["total"] or 0
        succeeded = agg["succeeded"] or 0
        # 成功率将 PARTIAL 视为部分成功，仅完全成功计入分子，保留一位小数。
        success_rate = round(succeeded / total * 100, 1) if total else 0.0

        return SyncStats(
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


class SyncAlert(models.Model):
    """同步告警记录.

    同步失败（达最大重试仍失败）时产生告警，供监控面板展示与确认处理。
    """

    objects: models.Manager[SyncAlert]

    config = models.ForeignKey(
        SyncConfig,
        on_delete=models.CASCADE,
        related_name="alerts",
        verbose_name="同步配置",
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
        verbose_name = "同步告警"
        verbose_name_plural = "同步告警"
        ordering = ["-created_at"]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        return f"[{self.level}] {self.config.name}: {self.message[:40]}"  # type: ignore[bad-return]

    @classmethod
    def raise_alert(cls, config: SyncConfig, message: str, *, level: str = AlertLevel.ERROR) -> SyncAlert:
        """为指定配置创建一条告警记录.

        Args:
            config: 触发告警的同步配置。
            message: 告警内容（通常为失败原因）。
            level: 告警级别，默认 ERROR。

        Returns:
            SyncAlert: 新建的告警记录。
        """
        return cls.objects.create(config=config, level=level, message=message)

    def acknowledge(self, *, save: bool = True) -> None:
        """确认告警（标记已处理并记录确认时间）."""
        self.acknowledged = True
        self.acknowledged_at = timezone.now()
        if save:
            self.save(update_fields=["acknowledged", "acknowledged_at"])


__all__ = [
    "AlertLevel",
    "ConflictStrategy",
    "SyncAlert",
    "SyncConfig",
    "SyncFieldMapping",
    "SyncLog",
    "SyncLogStatus",
    "SyncMode",
    "SyncStats",
    "SyncStatus",
]
