"""system Router - 系统运维接口.

仅管理员可访问。提供：

- GET /system/health：详细健康检查（DB/磁盘/Redis/连接池）
- GET /system/pool-stats：数据源连接池状态
- GET /system/circuit-states：熔断器状态
- GET /system/locks：分布式锁状态
- POST /system/backup：触发备份（异步，返回 task_id）
- GET /system/backups：备份归档列表
- GET /system/backups/{filename}：下载备份归档
- GET /system/backup-tasks/{task_id}：查询备份/恢复任务状态
- POST /system/restore：触发恢复（需 confirm=true，自动创建 pre-restore 快照）
- GET /system/audit/verify：审计哈希链校验
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin
from apps.audit.audit import log_audit
from apps.audit.hashchain import verify_chain
from apps.audit.models import AuditAction, AuditLog

from .backup_service import backup_file_path, list_backups, trigger_backup, trigger_restore
from .circuit_breaker import list_breakers
from .distributed_lock import list_lock_info
from .health import build_health
from .models import BackupTask
from .pool_monitor import collect_pool_stats
from .schemas import (
    AuditVerifyOut,
    BackupFileInfoOut,
    BackupListOut,
    BackupTaskOut,
    BackupTriggerOut,
    ChainBreakOut,
    CircuitStateOut,
    CircuitStatesOut,
    HealthOut,
    LockInfoOut,
    LockListOut,
    PoolStatOut,
    PoolStatsOut,
    RestoreTriggerIn,
)

router = Router(tags=["system"], auth=JWTAuth())


@router.get("/health", response={200: HealthOut})
def health_view(request: HttpRequest) -> HttpResponse:
    """详细健康检查（仅管理员）."""
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
    """熔断器状态（仅管理员）."""
    require_admin(request)
    items = [CircuitStateOut(**b.snapshot()).model_dump() for b in list_breakers()]
    body = CircuitStatesOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.get("/locks", response={200: LockListOut})
def locks_view(request: HttpRequest) -> HttpResponse:
    """分布式锁状态（仅管理员）."""
    require_admin(request)
    infos = list_lock_info()
    items = [LockInfoOut(name=i.name, held=i.held, ttl=i.ttl).model_dump() for i in infos]
    body = LockListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


# ---------- 备份/恢复 ----------


@router.post("/backup", response={200: BackupTriggerOut})
def backup_view(request: HttpRequest) -> HttpResponse:
    """触发数据库备份（仅管理员，异步执行）."""
    require_admin(request)
    user = request.auth
    task = trigger_backup(user)
    log_audit(
        request,
        action=AuditAction.BACKUP_CREATE,
        resource_type="backup_task",
        resource_id=str(task.pk),
        extra={"archive_name": ""},
    )
    body = BackupTriggerOut(
        task_id=task.pk,
        status=task.status,
        message="备份任务已创建，后台执行中",
    ).model_dump()
    return JsonResponse(body)


@router.get("/backups", response={200: BackupListOut})
def list_backups_view(request: HttpRequest) -> HttpResponse:
    """列出所有备份归档文件（仅管理员）."""
    require_admin(request)
    items = [BackupFileInfoOut(**item).model_dump() for item in list_backups()]
    body = BackupListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.get("/backups/{filename}")
def download_backup_view(request: HttpRequest, filename: str) -> HttpResponse:
    """下载备份归档文件（仅管理员）."""
    require_admin(request)
    path = backup_file_path(filename)
    if path is None:
        raise HttpError(404, "备份文件不存在")
    from django.http import FileResponse

    resp: HttpResponse = FileResponse(  # type: ignore[assignment]
        path.open("rb"), content_type="application/gzip"
    )
    resp["Content-Disposition"] = f'attachment; filename="{Path(filename).name}"'
    return resp


@router.get("/backup-tasks/{task_id}", response={200: BackupTaskOut})
def backup_task_view(request: HttpRequest, task_id: int) -> HttpResponse:
    """查询备份/恢复任务状态（仅管理员）."""
    require_admin(request)
    try:
        task = BackupTask.objects.get(pk=task_id)
    except BackupTask.DoesNotExist:
        raise HttpError(404, "任务不存在") from None
    body = BackupTaskOut(
        id=task.pk,
        action=task.action,
        status=task.status,
        archive_name=task.archive_name,
        archive_size=task.archive_size,
        engine=task.engine,
        error_message=task.error_message,
        created_at=task.created_at,
        completed_at=task.completed_at,
    ).model_dump()
    return JsonResponse(body)


@router.post("/restore", response={200: BackupTriggerOut})
def restore_view(request: HttpRequest, payload: RestoreTriggerIn) -> HttpResponse:
    """触发数据库恢复（仅管理员，需 confirm=true）.

    恢复前自动创建 pre-restore 快照。请求体::

        {"archive_name": "rdbase-backup-20260808-120000.tar.gz", "confirm": true}
    """
    require_admin(request)
    if not payload.confirm:
        raise HttpError(400, "恢复操作不可逆，请添加 confirm=true 二次确认")
    path = backup_file_path(payload.archive_name)
    if path is None:
        raise HttpError(404, "备份归档不存在")
    user = request.auth
    task = trigger_restore(user, payload.archive_name)
    log_audit(
        request,
        action=AuditAction.BACKUP_RESTORE,
        resource_type="backup_task",
        resource_id=str(task.pk),
        extra={"archive_name": payload.archive_name},
    )
    body = BackupTriggerOut(
        task_id=task.pk,
        status=task.status,
        message="恢复任务已创建，后台执行中（已自动创建 pre-restore 快照）",
    ).model_dump()
    return JsonResponse(body)


# ---------- 审计哈希链校验 ----------


@router.get("/audit/verify", response={200: AuditVerifyOut})
def audit_verify_view(request: HttpRequest) -> HttpResponse:
    """校验审计日志哈希链完整性（仅管理员）."""
    require_admin(request)
    breaks = verify_chain()
    total = AuditLog.objects.count()
    log_audit(
        request,
        action=AuditAction.AUDIT_VERIFY,
        resource_type="audit_chain",
        extra={"breaks_count": len(breaks), "total_records": total},
    )
    body = AuditVerifyOut(
        valid=len(breaks) == 0,
        total_records=total,
        breaks=[ChainBreakOut(**b.to_dict()) for b in breaks],
    ).model_dump()
    return JsonResponse(body)


__all__ = ["router"]
