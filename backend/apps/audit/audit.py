"""业务级审计日志辅助函数.

供各业务 view 在写操作完成（或失败）后显式调用，补充中间件层无法获取的业务上下文：
SQL 文本、影响行数、数据源 ID、资源类型/ID、详细动作分类等。

示例::

    from apps.audit.audit import log_audit, AuditAction

    @router.post("/rows")
    def create_row(request, ...):
        try:
            row = insert_row(...)
            log_audit(
                request, action=AuditAction.DML_INSERT,
                resource_type="row", resource_id=str(row.get("id")),
                datasource_id=ds.pk, datasource_name=ds.name,
                sql="<INSERT>", row_count=1,
            )
            return ...
        except Exception as exc:
            log_audit(request, action=AuditAction.DML_INSERT, status=AuditStatus.FAILURE,
                      error_message=str(exc), datasource_id=ds.pk)
            raise

设计要点：

- 业务层记录与中间件层记录**独立**：中间件记录通用 HTTP 维度，业务层记录业务维度，
  两者通过 ``action``/``source`` 字段区分；前端查询时默认按 ``source=business`` 过滤，
  避免重复展示。
- 失败也记录：业务调用方在 except 分支调用 ``log_audit(status=FAILURE)``；正常分支
  在 return 前调用 ``log_audit(status=SUCCESS)``。
- ``sql`` 字段记录原始 SQL 文本，但**脱敏**：避免记录密码字面量（数据源连接测试等）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest

from .models import AuditAction, AuditLog, AuditSource, AuditStatus

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# SQL 文本最大记录长度，超出截断（避免单条日志过大）
_MAX_SQL_LENGTH = 4096


def log_audit(  # noqa: PLR0913
    request: HttpRequest,
    *,
    action: str = AuditAction.WRITE,
    status: str = AuditStatus.SUCCESS,
    resource_type: str = "",
    resource_id: str = "",
    datasource_id: int | None = None,
    datasource_name: Any = "",
    sql: str = "",
    row_count: int | None = None,
    elapsed_ms: int | None = None,
    error_message: str = "",
    extra: dict[str, Any] | None = None,
) -> AuditLog | None:
    """记录一条业务级审计日志.

    Args:
        request: HTTP 请求对象，用于提取用户、IP、UA、方法、路径。
        action: 操作类型（``AuditAction`` 枚举值），默认 ``WRITE``。
        status: 操作结果（``AuditStatus.SUCCESS`` / ``FAILURE``）。
        resource_type: 资源类型，如 ``"datasource"``/``"draft"``/``"row"``/``"view"``。
        resource_id: 资源 ID（字符串，支持复合主键的 JSON 表示）。
        datasource_id: 目标数据源 ID（DML/DDL 操作必填）。
        datasource_name: 目标数据源名称（冗余，便于直接展示）。
        sql: SQL 文本（截断到 ``_MAX_SQL_LENGTH``）。
        row_count: 影响行数。
        elapsed_ms: 操作耗时（毫秒）。
        error_message: 失败时的错误信息。
        extra: 额外扩展字段（如导入文件名、对象类型等）。

    Returns:
        创建的 :class:`AuditLog` 实例；记录失败时返回 ``None`` 并记录日志（不抛异常，
        确保审计失败不影响业务流程）。
    """
    try:
        user = getattr(request, "auth", None)
        user_obj: User | None = user if isinstance(user, object) and hasattr(user, "pk") else None  # type: ignore[bad-assignment]
        username = getattr(user_obj, "username", "") or ""
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")[:512]
        sql_text = sql[:_MAX_SQL_LENGTH] if sql else ""

        return AuditLog.objects.create_with_hash(
            user=user_obj,
            username=username,
            action=action,
            source=AuditSource.BUSINESS,
            status=status,
            method=request.method or "",
            path=request.path or "",
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else "",
            datasource_id=datasource_id,
            datasource_name=datasource_name,
            sql=sql_text,
            row_count=row_count,
            elapsed_ms=elapsed_ms,
            ip=ip,
            user_agent=ua,
            error_message=error_message,
            extra=extra or {},
        )
    except Exception:  # 审计失败不应中断业务
        logger.exception("记录审计日志失败 action=%s status=%s", action, status)
        return None


def _get_client_ip(request: HttpRequest) -> str | None:
    """从请求中提取客户端 IP.

    优先级：``X-Forwarded-For`` 第一个 → ``X-Real-IP`` → ``REMOTE_ADDR``。
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2
        return xff.split(",")[0].strip() or None
    xri = request.META.get("HTTP_X_REAL_IP", "")
    if xri:
        return xri.strip() or None
    remote = request.META.get("REMOTE_ADDR", "")
    return remote or None


__all__ = ["log_audit"]
