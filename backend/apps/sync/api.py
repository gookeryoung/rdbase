"""sync Router — 数据同步管理接口.

提供：
- GET /sync/configs：列出同步配置
- POST /sync/configs：创建同步配置
- GET /sync/configs/{id}：获取单个配置
- PATCH /sync/configs/{id}：更新配置
- DELETE /sync/configs/{id}：删除配置
- POST /sync/configs/{id}/trigger：触发同步执行
- POST /sync/configs/{id}/preview：预览同步数据
- POST /sync/batch-trigger：批量触发同步
- GET /sync/logs：列出同步日志
- GET /sync/source-columns：获取源表列信息
- GET /sync/target-columns：获取目标表列信息
- POST /sync/scheduled：执行定时同步
"""

from __future__ import annotations

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin
from apps.datasources.engine import get_engine as get_ds_engine
from apps.datasources.models import DataSource

from .models import AlertLevel, ConflictStrategy, SyncAlert, SyncConfig, SyncFieldMapping, SyncLog
from .scheduling import CronError, validate_cron
from .schemas import (
    MessageOut,
    SyncAlertListOut,
    SyncAlertOut,
    SyncBatchIn,
    SyncBatchOut,
    SyncConfigCreateIn,
    SyncConfigListOut,
    SyncConfigOut,
    SyncConfigUpdateIn,
    SyncFieldMappingOut,
    SyncLogListOut,
    SyncLogOut,
    SyncPreviewOut,
    SyncResultOut,
    SyncScheduleIn,
    SyncSourceTableOut,
    SyncStatsOut,
    SyncTargetTableOut,
    SyncTriggerIn,
)
from .sync_service import BatchSyncResult, SyncError, SyncPreview, SyncService

router = Router(tags=["sync"], auth=JWTAuth())


def _config_to_out(config: SyncConfig) -> SyncConfigOut:
    """将 SyncConfig 模型转为 SyncConfigOut."""
    mappings = config.field_mappings.all()
    field_mappings = [
        SyncFieldMappingOut(
            id=m.pk,
            config_id=config.pk,
            source_field=m.source_field,
            target_field=m.target_field,
            mapping_type=m.mapping_type,
            fixed_value=m.fixed_value,
            is_pk=m.is_pk,
        )
        for m in mappings
    ]
    return SyncConfigOut(
        id=config.pk,
        name=config.name,
        description=config.description,
        source_table=config.source_table,
        source_schema=config.source_schema,
        source_db_alias=config.source_db_alias,
        target_datasource_id=config.target_datasource_id,
        target_table=config.target_table,
        target_schema=config.target_schema,
        sync_mode=config.sync_mode,
        status=config.status,
        conflict_strategy=config.conflict_strategy,
        timestamp_field=config.timestamp_field,
        batch_size=config.batch_size,
        scheduler_enabled=config.scheduler_enabled,
        cron_expression=config.cron_expression,
        last_run_at=config.last_run_at,
        next_run_at=config.next_run_at,
        retry_count=config.retry_count,
        max_retries=config.max_retries,
        created_by_id=config.created_by_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
        last_sync_at=config.last_sync_at,
        field_mappings=field_mappings,
    )


def _log_to_out(log: SyncLog) -> SyncLogOut:
    """将 SyncLog 模型转为 SyncLogOut."""
    return SyncLogOut(
        id=log.pk,
        config_id=log.config_id,
        status=log.status,
        mode=log.mode,
        rows_read=log.rows_read,
        rows_written=log.rows_written,
        rows_skipped=log.rows_skipped,
        error_message=log.error_message,
        started_at=log.started_at,
        finished_at=log.finished_at,
        duration_ms=log.duration_ms,
    )


def _preview_to_out(preview: SyncPreview) -> SyncPreviewOut:
    """将 SyncPreview 转为 SyncPreviewOut."""
    return SyncPreviewOut(
        config_id=preview.config_id,
        config_name=preview.config_name,
        mode=preview.mode,
        total_rows=preview.total_rows,
        sample_rows=preview.sample_rows,
        target_fields=preview.target_fields,
        pk_fields=preview.pk_fields,
        can_sync=preview.can_sync,
        error_message=preview.error_message,
    )


def _alert_to_out(alert: SyncAlert) -> SyncAlertOut:
    """将 SyncAlert 模型转为 SyncAlertOut."""
    return SyncAlertOut(
        id=alert.pk,
        config_id=alert.config_id,
        config_name=alert.config.name,
        level=alert.level,
        message=alert.message,
        acknowledged=alert.acknowledged,
        acknowledged_at=alert.acknowledged_at,
        created_at=alert.created_at,
    )


def _batch_result_to_out(result: BatchSyncResult) -> SyncBatchOut:
    """将 BatchSyncResult 转为 SyncBatchOut."""
    results = [
        SyncResultOut(
            log_id=log.pk,
            status=log.status,
            mode=log.mode,
            rows_read=log.rows_read,
            rows_written=log.rows_written,
            rows_skipped=log.rows_skipped,
            error_message=log.error_message,
            duration_ms=log.duration_ms,
        )
        for log in result.results
    ]
    return SyncBatchOut(
        total=result.total,
        succeeded=result.succeeded,
        failed=result.failed,
        skipped=result.skipped,
        results=results,
    )


def _validate_cron_or_400(scheduler_enabled: bool, cron_expression: str) -> None:
    """启用调度时校验 cron 表达式，非法则抛出 400.

    未启用调度时跳过校验（允许保存空 cron 或占位内容）。
    """
    if scheduler_enabled:
        try:
            validate_cron(cron_expression)
        except CronError as exc:
            raise HttpError(400, str(exc)) from exc


def _validate_conflict_strategy_or_400(strategy: str) -> None:
    """校验冲突处理策略取值合法，非法则抛出 400."""
    valid = {choice.value for choice in ConflictStrategy}
    if strategy not in valid:
        raise HttpError(400, f"非法的冲突处理策略：{strategy}（可选：{', '.join(sorted(valid))}）")


# ================================================================
# 同步配置 CRUD
# ================================================================


@router.get("/configs", response={200: SyncConfigListOut})
def list_configs(request: HttpRequest) -> HttpResponse:
    """列出所有同步配置（仅管理员）."""
    require_admin(request)
    qs = SyncConfig.objects.all().order_by("-id")
    items = [_config_to_out(c) for c in qs]
    body = SyncConfigListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.post("/configs", response={200: SyncConfigOut})
def create_config(request: HttpRequest, payload: SyncConfigCreateIn) -> HttpResponse:
    """创建同步配置（仅管理员）."""
    require_admin(request)

    # 获取当前用户
    user = getattr(request, "auth", None) or request.user
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "未认证")

    # 校验目标数据源
    try:
        DataSource.objects.get(pk=payload.target_datasource_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"数据源 {payload.target_datasource_id} 不存在") from None

    # 启用调度时校验 cron 表达式
    _validate_cron_or_400(payload.scheduler_enabled, payload.cron_expression)
    # 校验冲突处理策略
    _validate_conflict_strategy_or_400(payload.conflict_strategy)

    with transaction.atomic():
        config = SyncConfig.objects.create(
            name=payload.name,
            description=payload.description,
            source_table=payload.source_table,
            source_schema=payload.source_schema,
            source_db_alias=payload.source_db_alias,
            target_datasource_id=payload.target_datasource_id,
            target_table=payload.target_table,
            target_schema=payload.target_schema,
            sync_mode=payload.sync_mode,
            status=payload.status,
            conflict_strategy=payload.conflict_strategy,
            timestamp_field=payload.timestamp_field,
            batch_size=payload.batch_size,
            scheduler_enabled=payload.scheduler_enabled,
            cron_expression=payload.cron_expression,
            max_retries=payload.max_retries,
            created_by=user,
        )
        # 创建字段映射
        for fm in payload.field_mappings:
            SyncFieldMapping.objects.create(
                config=config,
                source_field=fm.source_field,
                target_field=fm.target_field,
                mapping_type=fm.mapping_type,
                fixed_value=fm.fixed_value,
                is_pk=fm.is_pk,
            )

    # 若启用调度则计算首次 next_run_at
    config.refresh_next_run()

    body = _config_to_out(config).model_dump()
    return JsonResponse(body)


@router.get("/configs/{config_id}", response={200: SyncConfigOut})
def get_config(request: HttpRequest, config_id: int) -> HttpResponse:
    """获取单个同步配置."""
    require_admin(request)
    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None
    return JsonResponse(_config_to_out(config).model_dump())


@router.patch("/configs/{config_id}", response={200: SyncConfigOut})
def update_config(
    request: HttpRequest,
    config_id: int,
    payload: SyncConfigUpdateIn,
) -> HttpResponse:
    """更新同步配置."""
    require_admin(request)
    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None

    data = payload.model_dump(exclude_unset=True, exclude={"field_mappings"})
    for key, value in data.items():
        setattr(config, key, value)

    # 依据更新后的最终状态校验 cron 表达式
    _validate_cron_or_400(config.scheduler_enabled, config.cron_expression)
    # 若更新了冲突处理策略则校验其合法性
    if "conflict_strategy" in data:
        _validate_conflict_strategy_or_400(config.conflict_strategy)

    config.save()

    # 更新字段映射（全量替换）
    if payload.field_mappings is not None:
        config.field_mappings.all().delete()
        for fm in payload.field_mappings:
            SyncFieldMapping.objects.create(
                config=config,
                source_field=fm.source_field,
                target_field=fm.target_field,
                mapping_type=fm.mapping_type,
                fixed_value=fm.fixed_value,
                is_pk=fm.is_pk,
            )

    # 调度相关字段可能变更，刷新下次执行时间
    config.refresh_next_run()

    return JsonResponse(_config_to_out(config).model_dump())


@router.delete("/configs/{config_id}", response={200: MessageOut})
def delete_config(request: HttpRequest, config_id: int) -> HttpResponse:
    """删除同步配置."""
    require_admin(request)
    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None
    config.delete()
    return JsonResponse(MessageOut(detail=f"已删除同步配置 {config_id}").model_dump())


# ================================================================
# 同步执行
# ================================================================


@router.post("/configs/{config_id}/trigger", response={200: SyncResultOut})
def trigger_sync(
    request: HttpRequest,
    config_id: int,
    payload: SyncTriggerIn,
) -> HttpResponse:
    """手动触发同步执行."""
    require_admin(request)
    if not payload.confirm:
        raise HttpError(400, "须确认操作（confirm=true）")

    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None

    if not config.is_active:
        raise HttpError(400, "同步配置已暂停，请先启用")

    try:
        service = SyncService(config)
        log = service.run(force_full=payload.force_full)
    except SyncError as exc:
        raise HttpError(500, str(exc)) from exc

    body = SyncResultOut(
        log_id=log.pk,
        status=log.status,
        mode=log.mode,
        rows_read=log.rows_read,
        rows_written=log.rows_written,
        rows_skipped=log.rows_skipped,
        error_message=log.error_message,
        duration_ms=log.duration_ms,
    ).model_dump()
    return JsonResponse(body)


# ================================================================
# 同步日志
# ================================================================


@router.get("/logs", response={200: SyncLogListOut})
def list_logs(
    request: HttpRequest,
    config_id: int | None = None,
    limit: int = 50,
) -> HttpResponse:
    """列出同步日志."""
    require_admin(request)
    qs = SyncLog.objects.all().order_by("-started_at")
    if config_id is not None:
        qs = qs.filter(config_id=config_id)
    qs = qs[:limit]
    items = [_log_to_out(log) for log in qs]
    total = SyncLog.objects.count()
    body = SyncLogListOut(items=items, total=total).model_dump()
    return JsonResponse(body)


# ================================================================
# 监控与告警
# ================================================================


@router.get("/stats", response={200: SyncStatsOut})
def get_stats(
    request: HttpRequest,
    config_id: int | None = None,
    days: int | None = None,
) -> HttpResponse:
    """获取同步统计（成功率、平均耗时、总读写行数）.

    可按 config_id 过滤到单个配置，按 days 限定最近 N 天（不传则统计全部）。
    """
    require_admin(request)
    stats = SyncLog.aggregate_stats(config_id=config_id, days=days)
    body = SyncStatsOut(
        total=stats.total,
        succeeded=stats.succeeded,
        partial=stats.partial,
        failed=stats.failed,
        success_rate=stats.success_rate,
        avg_duration_ms=stats.avg_duration_ms,
        total_rows_read=stats.total_rows_read,
        total_rows_written=stats.total_rows_written,
        total_rows_skipped=stats.total_rows_skipped,
    ).model_dump()
    return JsonResponse(body)


@router.get("/alerts", response={200: SyncAlertListOut})
def list_alerts(
    request: HttpRequest,
    config_id: int | None = None,
    acknowledged: bool | None = None,
    level: str | None = None,
    limit: int = 50,
) -> HttpResponse:
    """列出同步告警.

    支持按 config_id、acknowledged（是否已确认）、level（告警级别）过滤。
    返回项内含未确认告警总数，便于前端展示待处理数量徽标。
    """
    require_admin(request)

    if level is not None and level not in {choice.value for choice in AlertLevel}:
        raise HttpError(400, f"非法的告警级别：{level}")

    qs = SyncAlert.objects.select_related("config").order_by("-created_at")
    if config_id is not None:
        qs = qs.filter(config_id=config_id)
    if acknowledged is not None:
        qs = qs.filter(acknowledged=acknowledged)
    if level is not None:
        qs = qs.filter(level=level)

    items = [_alert_to_out(a) for a in qs[:limit]]
    total = SyncAlert.objects.count()
    unacknowledged = SyncAlert.objects.filter(acknowledged=False).count()
    body = SyncAlertListOut(items=items, total=total, unacknowledged=unacknowledged).model_dump()
    return JsonResponse(body)


@router.post("/alerts/{alert_id}/ack", response={200: SyncAlertOut})
def acknowledge_alert(request: HttpRequest, alert_id: int) -> HttpResponse:
    """确认告警（标记为已处理）."""
    require_admin(request)
    try:
        alert = SyncAlert.objects.select_related("config").get(pk=alert_id)
    except SyncAlert.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"告警 {alert_id} 不存在") from None
    alert.acknowledge()
    return JsonResponse(_alert_to_out(alert).model_dump())


# ================================================================
# 辅助接口
# ================================================================


@router.get("/source-columns", response={200: SyncSourceTableOut})
def get_source_columns(
    request: HttpRequest,
    table: str = "",
    schema_name: str = "",  # noqa: ARG001
) -> HttpResponse:
    """获取源表（rdbase 平台库）的列信息."""
    require_admin(request)
    if not table:
        raise HttpError(400, "须指定表名 table")

    # 从 Django 默认数据库读取表结构
    from django.db import connection as django_conn

    try:
        with django_conn.cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = []
            for row in cursor.fetchall():
                columns.append(
                    {
                        "name": row[1],
                        "type": row[2],
                        "notnull": bool(row[3]),
                        "pk": bool(row[5]),
                    }
                )
    except Exception as exc:
        raise HttpError(500, f"读取源表结构失败: {exc}") from exc

    body = SyncSourceTableOut(table_name=table, columns=columns).model_dump()
    return JsonResponse(body)


@router.get("/target-columns", response={200: SyncTargetTableOut})
def get_target_columns(
    request: HttpRequest,
    datasource_id: int = 0,
    table: str = "",
    schema_name: str = "",
) -> HttpResponse:
    """获取目标数据源表的列信息."""
    require_admin(request)
    if not datasource_id or not table:
        raise HttpError(400, "须指定 datasource_id 和 table")

    try:
        ds = DataSource.objects.get(pk=datasource_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"数据源 {datasource_id} 不存在") from None

    try:
        engine = get_ds_engine(ds)
        from sqlalchemy import inspect

        insp = inspect(engine)
        effective_schema = schema_name or None
        columns_info = insp.get_columns(table, schema=effective_schema)
        pk_info = insp.get_pk_constraint(table, schema=effective_schema)
        pk_cols = set(pk_info.get("constrained_columns", []))

        columns = [
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "pk": col["name"] in pk_cols,
            }
            for col in columns_info
        ]
    except Exception as exc:
        raise HttpError(500, f"读取目标表结构失败: {exc}") from exc

    body = SyncTargetTableOut(table_name=table, columns=columns).model_dump()
    return JsonResponse(body)


# ================================================================
# 预览与批量同步
# ================================================================


@router.post("/configs/{config_id}/preview", response={200: SyncPreviewOut})
def preview_sync(
    request: HttpRequest,
    config_id: int,
    payload: SyncTriggerIn,
) -> HttpResponse:
    """预览同步数据（不实际执行）."""
    require_admin(request)

    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None

    service = SyncService(config)
    preview = service.preview(force_full=payload.force_full)
    return JsonResponse(_preview_to_out(preview).model_dump())


@router.post("/batch-trigger", response={200: SyncBatchOut})
def batch_trigger_sync(
    request: HttpRequest,
    payload: SyncBatchIn,
) -> HttpResponse:
    """批量触发同步."""
    require_admin(request)
    if not payload.confirm:
        raise HttpError(400, "须确认操作（confirm=true）")

    result = SyncService.run_batch(
        payload.config_ids,
        force_full=payload.force_full,
        stop_on_error=payload.stop_on_error,
        max_workers=payload.max_workers,
    )
    return JsonResponse(_batch_result_to_out(result).model_dump())


@router.post("/scheduled", response={200: SyncBatchOut})
def run_scheduled_sync(request: HttpRequest) -> HttpResponse:
    """执行所有可调度的同步配置."""
    require_admin(request)

    result = SyncService.run_scheduled()
    return JsonResponse(_batch_result_to_out(result).model_dump())


@router.post("/configs/{config_id}/schedule", response={200: SyncConfigOut})
def update_schedule(
    request: HttpRequest,
    config_id: int,
    payload: SyncScheduleIn,
) -> HttpResponse:
    """更新同步配置的调度设置."""
    require_admin(request)

    try:
        config = SyncConfig.objects.get(pk=config_id)
    except SyncConfig.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"同步配置 {config_id} 不存在") from None

    # 启用调度时校验 cron 表达式
    _validate_cron_or_400(payload.scheduler_enabled, payload.cron_expression)

    config.scheduler_enabled = payload.scheduler_enabled
    config.cron_expression = payload.cron_expression
    config.max_retries = payload.max_retries
    config.save()
    # 调度设置变更后刷新下次执行时间
    config.refresh_next_run()

    return JsonResponse(_config_to_out(config).model_dump())


__all__ = ["router"]
