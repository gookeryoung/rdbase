"""数据摄取 Router — 外部数据爬取管理接口.

提供：
- GET /ingest/tasks：列出爬取任务
- POST /ingest/tasks：创建爬取任务（管理员）
- GET /ingest/tasks/{id}：获取任务详情
- PUT /ingest/tasks/{id}：更新任务（管理员）
- DELETE /ingest/tasks/{id}：删除任务（管理员）
- POST /ingest/tasks/{id}/run：手动触发执行（管理员，子进程运行 Scrapy）
- GET /ingest/tasks/{id}/logs：列出任务执行日志
- GET /ingest/alerts：列出告警
- POST /ingest/alerts/{id}/ack：确认告警（管理员）
- GET /ingest/stats：爬取统计

写操作由 AuditMiddleware 中间件层捕获为通用 WRITE 审计；
ingest 专用审计枚举与显式业务上下文记录在 iter-35 补全。
"""

from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin
from apps.datasources.models import DataSource
from apps.ingest.engine import spawn_ingest
from apps.ingest.models import (
    AuthType,
    ConflictStrategy,
    IngestAlert,
    IngestFieldMapping,
    IngestLog,
    IngestTask,
    SourceType,
)
from apps.ingest.schemas import (
    IngestAlertOut,
    IngestFieldMappingOut,
    IngestLogOut,
    IngestRunOut,
    IngestStatsOut,
    IngestTaskCreateIn,
    IngestTaskOut,
    IngestTaskUpdateIn,
    MessageOut,
)
from apps.sync.scheduling import CronError, validate_cron

router = Router(tags=["ingest"], auth=JWTAuth())

_VALID_SOURCE_TYPES = {st.value for st in SourceType}
_VALID_AUTH_TYPES = {a.value for a in AuthType}
_VALID_CONFLICT_STRATEGIES = {c.value for c in ConflictStrategy}


def _get_task_or_404(task_id: int) -> IngestTask:
    """按 ID 获取爬取任务，不存在抛 404."""
    try:
        return IngestTask.objects.get(pk=task_id)
    except IngestTask.DoesNotExist as exc:
        raise HttpError(404, "爬取任务不存在") from exc


def _mapping_to_out(m: IngestFieldMapping) -> IngestFieldMappingOut:
    """字段映射模型转输出."""
    return IngestFieldMappingOut(
        id=m.pk,
        task_id=m.task_id,
        source_field=m.source_field,
        target_field=m.target_field,
        mapping_type=m.mapping_type,
        fixed_value=m.fixed_value,
        is_pk=m.is_pk,
    )


def _task_to_out(task: IngestTask) -> IngestTaskOut:
    """爬取任务模型转输出（headers 不回显明文，仅返回 has_headers 标志）."""
    mappings = [_mapping_to_out(m) for m in task.field_mappings.all()]
    return IngestTaskOut(
        id=task.pk,
        name=task.name,
        description=task.description,
        source_type=task.source_type,
        source_url=task.source_url,
        parse_config=cast(dict[str, Any], task.parse_config or {}),
        request_config=cast(dict[str, Any], task.request_config or {}),
        has_headers=bool(task.headers_encrypted),
        auth_type=task.auth_type,
        target_datasource_id=task.target_datasource_id,
        target_table=task.target_table,
        conflict_strategy=task.conflict_strategy,
        batch_size=task.batch_size,
        obey_robots=bool(task.obey_robots),
        scheduler_enabled=bool(task.scheduler_enabled),
        cron_expression=task.cron_expression,
        next_run_at=task.next_run_at,
        last_run_at=task.last_run_at,
        last_sync_at=task.last_sync_at,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        status=task.status,
        created_by_id=task.created_by_id,
        created_at=task.created_at,  # type: ignore[missing-attribute]
        updated_at=task.updated_at,  # type: ignore[missing-attribute]
        field_mappings=mappings,
    )


def _log_to_out(log: IngestLog) -> IngestLogOut:
    """日志模型转输出."""
    return IngestLogOut(
        id=log.pk,
        task_id=log.task_id,
        status=log.status,
        rows_read=log.rows_read,
        rows_written=log.rows_written,
        rows_skipped=log.rows_skipped,
        error_message=log.error_message,
        started_at=log.started_at,
        finished_at=log.finished_at,
        duration_ms=log.duration_ms,
    )


def _validate_task_fields(  # noqa: PLR0913
    *,
    source_type: str,
    auth_type: str,
    conflict_strategy: str,
    target_datasource_id: int,
    scheduler_enabled: bool,
    cron_expression: str,
) -> None:
    """校验任务字段合法性与目标数据源存在性.

    Raises:
        HttpError: 字段非法或目标数据源不存在时抛 400/404。
    """
    if source_type not in _VALID_SOURCE_TYPES:
        raise HttpError(400, f"无效的源类型: {source_type}")
    if auth_type not in _VALID_AUTH_TYPES:
        raise HttpError(400, f"无效的鉴权类型: {auth_type}")
    if conflict_strategy not in _VALID_CONFLICT_STRATEGIES:
        raise HttpError(400, f"无效的冲突策略: {conflict_strategy}")
    if not DataSource.objects.filter(pk=target_datasource_id).exists():
        raise HttpError(404, "目标数据源不存在")
    if scheduler_enabled and cron_expression:
        try:
            validate_cron(cron_expression)
        except CronError as exc:
            raise HttpError(400, str(exc)) from exc


def _sync_field_mappings(task: IngestTask, mappings: list[Any]) -> None:
    """全量替换任务的字段映射."""
    task.field_mappings.all().delete()
    objs = [
        IngestFieldMapping(
            task=task,
            source_field=m.source_field,
            target_field=m.target_field,
            mapping_type=m.mapping_type,
            fixed_value=m.fixed_value,
            is_pk=m.is_pk,
        )
        for m in mappings
    ]
    if objs:
        IngestFieldMapping.objects.bulk_create(objs)


@router.get("/tasks", response=list[IngestTaskOut])
def list_tasks(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    """列出全部爬取任务（所有登录用户可访问）."""
    tasks = IngestTask.objects.all().order_by("-id")
    out = [_task_to_out(t) for t in tasks]
    body = [o.model_dump(mode="json") for o in out]
    return JsonResponse(body, safe=False)


@router.post("/tasks", response={201: IngestTaskOut})
def create_task(request: HttpRequest, payload: IngestTaskCreateIn) -> HttpResponse:
    """创建爬取任务（仅管理员）."""
    require_admin(request)
    _validate_task_fields(
        source_type=payload.source_type,
        auth_type=payload.auth_type,
        conflict_strategy=payload.conflict_strategy,
        target_datasource_id=payload.target_datasource_id,
        scheduler_enabled=payload.scheduler_enabled,
        cron_expression=payload.cron_expression,
    )
    task = IngestTask(
        name=payload.name,
        description=payload.description,
        source_type=payload.source_type,
        source_url=payload.source_url,
        parse_config=payload.parse_config,
        request_config=payload.request_config,
        auth_type=payload.auth_type,
        target_datasource_id=payload.target_datasource_id,
        target_table=payload.target_table,
        conflict_strategy=payload.conflict_strategy,
        batch_size=payload.batch_size,
        obey_robots=payload.obey_robots,
        scheduler_enabled=payload.scheduler_enabled,
        cron_expression=payload.cron_expression,
        created_by=request.auth,
    )
    if payload.headers:
        task.set_headers(payload.headers)
    task.save()
    _sync_field_mappings(task, payload.field_mappings)
    if task.is_schedulable:
        task.refresh_next_run()
    body = _task_to_out(task).model_dump(mode="json")
    return JsonResponse(body, status=201)


@router.get("/tasks/{task_id}", response=IngestTaskOut)
def get_task(request: HttpRequest, task_id: int) -> HttpResponse:  # noqa: ARG001
    """获取爬取任务详情."""
    task = _get_task_or_404(task_id)
    body = _task_to_out(task).model_dump(mode="json")
    return JsonResponse(body)


@router.put("/tasks/{task_id}", response=IngestTaskOut)
def update_task(request: HttpRequest, task_id: int, payload: IngestTaskUpdateIn) -> HttpResponse:
    """更新爬取任务（仅管理员，全量更新）."""
    require_admin(request)
    task = _get_task_or_404(task_id)
    data = payload.model_dump(exclude_unset=True)

    source_type = data.get("source_type", task.source_type)
    auth_type = data.get("auth_type", task.auth_type)
    conflict_strategy = data.get("conflict_strategy", task.conflict_strategy)
    target_datasource_id = data.get("target_datasource_id", task.target_datasource_id)
    scheduler_enabled = data.get("scheduler_enabled", task.scheduler_enabled)
    cron_expression = data.get("cron_expression", task.cron_expression)
    _validate_task_fields(
        source_type=source_type,
        auth_type=auth_type,
        conflict_strategy=conflict_strategy,
        target_datasource_id=target_datasource_id,
        scheduler_enabled=scheduler_enabled,
        cron_expression=cron_expression,
    )

    for field in (
        "name",
        "description",
        "source_type",
        "source_url",
        "parse_config",
        "request_config",
        "auth_type",
        "target_datasource_id",
        "target_table",
        "conflict_strategy",
        "batch_size",
        "obey_robots",
        "scheduler_enabled",
        "cron_expression",
        "status",
    ):
        if field in data:
            setattr(task, field, data[field])
    if "headers" in data:
        task.set_headers(data["headers"])
    task.save()
    if "field_mappings" in data:
        _sync_field_mappings(task, payload.field_mappings or [])
    task.refresh_next_run()
    body = _task_to_out(task).model_dump(mode="json")
    return JsonResponse(body)


@router.delete("/tasks/{task_id}", response=MessageOut)
def delete_task(request: HttpRequest, task_id: int) -> HttpResponse:
    """删除爬取任务（仅管理员）."""
    require_admin(request)
    task = _get_task_or_404(task_id)
    name = task.name
    task.delete()
    return JsonResponse({"message": f"已删除爬取任务: {name}"})


@router.post("/tasks/{task_id}/run", response=IngestRunOut)
def run_task(request: HttpRequest, task_id: int) -> HttpResponse:
    """手动触发爬取任务执行（仅管理员）.

    以子进程启动 ``run_ingest`` 命令运行 Scrapy，同步等待返回。
    大型爬取任务可能耗时较长，后续 iter-35 可改为异步。
    """
    require_admin(request)
    task = _get_task_or_404(task_id)
    result = spawn_ingest(task.pk)
    log = task.logs.order_by("-started_at").first()
    body = IngestRunOut(
        task_id=task.pk,
        returncode=result.returncode,
        log=_log_to_out(log) if log else None,
        stderr=result.stderr,
    ).model_dump(mode="json")
    return JsonResponse(body)


@router.get("/tasks/{task_id}/logs", response=list[IngestLogOut])
def list_task_logs(request: HttpRequest, task_id: int) -> HttpResponse:  # noqa: ARG001
    """列出指定任务的执行日志."""
    task = _get_task_or_404(task_id)
    logs = task.logs.order_by("-started_at")[:100]
    body = [_log_to_out(log).model_dump(mode="json") for log in logs]
    return JsonResponse(body, safe=False)


@router.get("/alerts", response=list[IngestAlertOut])
def list_alerts(request: HttpRequest) -> HttpResponse:
    """列出爬取告警（默认仅未确认，?all=true 返回全部）."""
    only_unacked = request.GET.get("all", "false").lower() != "true"
    qs = IngestAlert.objects.all()
    if only_unacked:
        qs = qs.filter(acknowledged=False)
    alerts = qs.order_by("-created_at")[:100]
    body = [
        IngestAlertOut(
            id=a.pk,
            task_id=a.task_id,
            level=a.level,
            message=a.message,
            acknowledged=a.acknowledged,
            acknowledged_at=a.acknowledged_at,
            created_at=a.created_at,  # type: ignore[missing-attribute]
        ).model_dump(mode="json")
        for a in alerts
    ]
    return JsonResponse(body, safe=False)


@router.post("/alerts/{alert_id}/ack", response=IngestAlertOut)
def acknowledge_alert(request: HttpRequest, alert_id: int) -> HttpResponse:
    """确认爬取告警（仅管理员）."""
    require_admin(request)
    try:
        alert = IngestAlert.objects.get(pk=alert_id)
    except IngestAlert.DoesNotExist as exc:
        raise HttpError(404, "告警不存在") from exc
    alert.acknowledge()
    body = IngestAlertOut(
        id=alert.pk,
        task_id=alert.task_id,
        level=alert.level,
        message=alert.message,
        acknowledged=alert.acknowledged,
        acknowledged_at=alert.acknowledged_at,
        created_at=alert.created_at,  # type: ignore[missing-attribute]
    ).model_dump(mode="json")
    return JsonResponse(body)


@router.get("/stats", response=IngestStatsOut)
def get_stats(request: HttpRequest) -> HttpResponse:
    """爬取统计（可选 ?days=30 限定最近天数）."""
    days_param = request.GET.get("days")
    days = int(days_param) if days_param and days_param.isdigit() else None
    stats = IngestLog.aggregate_stats(days=days)
    body = IngestStatsOut(
        total=stats.total,
        succeeded=stats.succeeded,
        partial=stats.partial,
        failed=stats.failed,
        success_rate=stats.success_rate,
        avg_duration_ms=stats.avg_duration_ms,
        total_rows_read=stats.total_rows_read,
        total_rows_written=stats.total_rows_written,
        total_rows_skipped=stats.total_rows_skipped,
    ).model_dump(mode="json")
    return JsonResponse(body)
