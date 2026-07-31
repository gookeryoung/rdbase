"""audit 模块 Router - 审计日志查询与导出接口.

仅管理员可访问。提供列表（分页/筛选）、详情、导出（CSV 流式）能力。

- GET ``/audit/logs``：分页查询审计日志，支持按用户/动作/资源/数据源/时间范围/状态筛选
- GET ``/audit/logs/{id}``：获取单条审计日志详情
- GET ``/audit/logs/export``：导出审计日志为 CSV（流式，含 UTF-8 BOM）
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin

from .models import AuditLog
from .schemas import AuditLogDetailOut, AuditLogListOut, AuditLogOut

router = Router(tags=["audit"], auth=JWTAuth())


def _log_dict(log: AuditLog) -> dict[str, Any]:
    """构造审计日志响应字典."""
    user_id = getattr(log, "user_id", None)
    return {
        "id": log.pk,  # type: ignore[no-any-return]
        "user_id": user_id,
        "username": log.username,
        "action": log.action,
        "source": log.source,
        "status": log.status,
        "method": log.method,
        "path": log.path,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "datasource_id": log.datasource_id,
        "datasource_name": log.datasource_name,
        "sql": log.sql,
        "row_count": log.row_count,
        "elapsed_ms": log.elapsed_ms,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "error_message": log.error_message,
        "extra": log.extra,
        "created_at": log.created_at.isoformat(),  # type: ignore[missing-attribute]
    }


def _parse_datetime(s: str | None) -> datetime | None:
    """解析 ISO 8601 日期时间字符串（含时区）.

    支持格式：``2026-07-31T10:00:00+08:00`` 或 ``2026-07-31 10:00:00``。
    输入为空或解析失败返回 ``None``。无时区信息时按当前时区处理。
    """
    if not s:
        return None
    try:
        # fromisoformat 支持 ``+08:00`` 与空格分隔
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # naive datetime 视为当前时区
            from django.utils import timezone as _tz

            return _tz.make_aware(dt, _tz.get_current_timezone())
        return dt
    except ValueError:
        return None


def _filter_qs(  # noqa: PLR0913
    qs: Any,
    *,
    user_id: int | None,
    username: str | None,
    action: str | None,
    source: str | None,
    status: str | None,
    resource_type: str | None,
    datasource_id: int | None,
    path: str | None,
    start: datetime | None,
    end: datetime | None,
) -> Any:
    """对查询集应用筛选条件（仅对非空参数过滤）."""
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if username:
        qs = qs.filter(username__icontains=username)
    if action:
        qs = qs.filter(action=action)
    if source:
        qs = qs.filter(source=source)
    if status:
        qs = qs.filter(status=status)
    if resource_type:
        qs = qs.filter(resource_type__icontains=resource_type)
    if datasource_id is not None:
        qs = qs.filter(datasource_id=datasource_id)
    if path:
        qs = qs.filter(path__icontains=path)
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lte=end)
    return qs


@router.get("/logs", response={200: AuditLogListOut})
def list_logs_view(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    user_id: int | None = None,
    username: str | None = None,
    action: str | None = None,
    source: str | None = None,
    status: str | None = None,
    resource_type: str | None = None,
    datasource_id: int | None = None,
    path: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> HttpResponse:
    """分页查询审计日志（仅管理员）.

    Query 参数：

        user_id: 按用户 ID 过滤。
        username: 按用户名模糊匹配。
        action: 操作类型（精确匹配，如 ``dml.insert``/``ddl.apply``）。
        source: 记录来源（``middleware``/``business``）。
        status: 操作结果（``success``/``failure``）。
        resource_type: 资源类型模糊匹配。
        datasource_id: 按数据源 ID 过滤。
        path: 请求路径模糊匹配。
        start: 起始时间（ISO 8601，含）。
        end: 截止时间（ISO 8601，含）。
        page: 页码，从 1 开始，默认 1。
        page_size: 每页条数，默认 20，最大 200。
    """
    require_admin(request)
    if page < 1:
        raise HttpError(400, "page 须 >= 1")
    if page_size < 1:
        raise HttpError(400, "page_size 须 >= 1")
    page_size = min(page_size, 200)
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    qs = AuditLog.objects.all()
    qs = _filter_qs(
        qs,
        user_id=user_id,
        username=username,
        action=action,
        source=source,
        status=status,
        resource_type=resource_type,
        datasource_id=datasource_id,
        path=path,
        start=start_dt,
        end=end_dt,
    )
    total = qs.count()
    items = list(qs.order_by("-id")[(page - 1) * page_size : page * page_size])
    body = AuditLogListOut(
        items=[AuditLogOut(**_log_dict(log)) for log in items],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()
    return JsonResponse(body)


# CSV 导出列顺序
_CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "username",
    "action",
    "source",
    "status",
    "method",
    "path",
    "resource_type",
    "resource_id",
    "datasource_id",
    "datasource_name",
    "sql",
    "row_count",
    "elapsed_ms",
    "ip",
    "user_agent",
    "error_message",
)


# 注意：/logs/export 必须注册在 /logs/{log_id} 之前，否则 ninja 会把 "export" 当作 log_id 解析
@router.get("/logs/export")
def export_logs_view(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    user_id: int | None = None,
    username: str | None = None,
    action: str | None = None,
    source: str | None = None,
    status: str | None = None,
    resource_type: str | None = None,
    datasource_id: int | None = None,
    path: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> HttpResponse:
    """导出审计日志为 CSV（仅管理员，流式响应）.

    导出列顺序见 ``_CSV_COLUMNS``。查询参数与 ``/logs`` 一致，但不分页（导出全部匹配项）。
    大数据量时通过 ``StreamingHttpResponse`` 分块产出，避免 OOM。
    """
    require_admin(request)
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    qs = AuditLog.objects.all()
    qs = _filter_qs(
        qs,
        user_id=user_id,
        username=username,
        action=action,
        source=source,
        status=status,
        resource_type=resource_type,
        datasource_id=datasource_id,
        path=path,
        start=start_dt,
        end=end_dt,
    ).order_by("-id")

    def _stream() -> Iterator[bytes]:
        # UTF-8 BOM，确保 Excel 正确识别中文
        yield b"\xef\xbb\xbf"
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_COLUMNS)
        yield buf.getvalue().encode("utf-8")
        # 按 1000 行分块查询，避免一次性加载全部到内存
        page_size = 1000
        seen = 0
        while True:
            chunk = list(qs[seen : seen + page_size])
            if not chunk:
                break
            for log in chunk:
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(_csv_row(log))
                yield buf.getvalue().encode("utf-8")
            seen += len(chunk)

    resp = StreamingHttpResponse(_stream(), content_type="text/csv; charset=utf-8")
    # 文件名带时间戳
    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    resp["Content-Disposition"] = f'attachment; filename="audit_logs_{ts}.csv"'
    return resp  # type: ignore[bad-return]


@router.get("/logs/{log_id}", response={200: AuditLogDetailOut})
def retrieve_log_view(request: HttpRequest, log_id: int) -> HttpResponse:
    """获取单条审计日志详情（仅管理员）."""
    require_admin(request)
    try:
        log = AuditLog.objects.get(pk=log_id)
    except AuditLog.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "审计日志不存在") from None
    body = AuditLogDetailOut(**_log_dict(log)).model_dump()
    return JsonResponse(body)


def _csv_row(log: AuditLog) -> list[Any]:
    """构造单行 CSV 数据（按 ``_CSV_COLUMNS`` 顺序）."""
    d = _log_dict(log)
    return [d.get(col, "") for col in _CSV_COLUMNS]


__all__ = ["router"]
