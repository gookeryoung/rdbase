"""审计日志中间件.

拦截所有 POST/PATCH/PUT/DELETE 请求，记录通用维度的审计信息：
用户/IP/方法/路径/状态码/耗时。业务维度（SQL/影响行数/资源类型等）由各业务 view
通过 ``apps.audit.audit.log_audit`` 辅助函数补充记录。

设计要点：

- 仅记录写操作（POST/PATCH/PUT/DELETE），GET/HEAD/OPTIONS 不记录（避免噪音）。
- 在 ``process_response`` 阶段记录（响应已生成，可获取状态码）。
- 中间件层记录的 ``action`` 默认为 ``AuditAction.WRITE``，业务层会再记录一条带具体
  action 的业务日志，两者通过 ``source`` 字段区分。
- 健康检查（``/health/``）与 OpenAPI 文档（``/api/v1/docs``）不记录。
- 审计失败不影响业务流程：捕获异常后仅记录日志，不抛出。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest, HttpResponse

from .models import AuditAction, AuditLog, AuditSource, AuditStatus

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# 触发审计的 HTTP 方法（写操作）
_AUDITED_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# 不审计的路径前缀（健康检查、API 文档、静态资源）
_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/api/v1/docs",
        "/api/v1/openapi",
        "/static/",
        "/admin/jsi18n/",
    }
)


class AuditMiddleware:
    """Django 中间件：拦截写操作并记录通用审计日志.

    在 ``MIDDLEWARE`` 配置中注册::

        MIDDLEWARE = [
            ...,
            "apps.audit.middleware.AuditMiddleware",
        ]

    每个写请求会生成一条 ``source=MIDDLEWARE`` 的 :class:`AuditLog` 记录，
    包含用户、IP、UA、方法、路径、状态码、耗时。业务上下文由 view 内显式调用
    :func:`apps.audit.audit.log_audit` 补充。
    """

    def __init__(self, get_response: Any) -> None:
        """初始化中间件.

        Args:
            get_response: Django 中间件链的下一环。
        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """处理请求：记录开始时间，调用下游，响应后写审计日志."""
        start = time.perf_counter()
        response = self.get_response(request)
        # 仅审计写操作且未被排除的路径
        if request.method in _AUDITED_METHODS and not _is_excluded(request.path):
            try:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                _record_middleware_audit(request, response, elapsed_ms)
            except Exception:  # 审计失败不阻断业务
                logger.exception("中间件记录审计日志失败 path=%s", request.path)
        return response


def _is_excluded(path: str) -> bool:
    """判断路径是否在排除列表中（前缀匹配）."""
    return any(path.startswith(p) for p in _EXCLUDED_PATHS)


def _record_middleware_audit(request: HttpRequest, response: HttpResponse, elapsed_ms: int) -> None:
    """记录一条中间件层审计日志."""
    user = getattr(request, "auth", None)
    user_obj: User | None = user if isinstance(user, object) and hasattr(user, "pk") else None  # type: ignore[bad-assignment]
    username = getattr(user_obj, "username", "") or ""

    status_code = getattr(response, "status_code", 0)
    status = AuditStatus.SUCCESS if 200 <= status_code < 400 else AuditStatus.FAILURE

    # 从请求体无法可靠提取 SQL 文本（已被 ninja 解析消费），中间件层不记录 sql 字段，
    # 业务层在 view 内显式补充。
    AuditLog.objects.create(
        user=user_obj,
        username=username,
        action=AuditAction.WRITE,
        source=AuditSource.MIDDLEWARE,
        status=status,
        method=request.method or "",
        path=request.path or "",
        elapsed_ms=elapsed_ms,
        ip=_get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
        error_message="" if status == AuditStatus.SUCCESS else f"HTTP {status_code}",
    )


def _get_client_ip(request: HttpRequest) -> str | None:
    """从请求中提取客户端 IP（与 audit._get_client_ip 同逻辑，避免跨模块依赖）."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip() or None
    xri = request.META.get("HTTP_X_REAL_IP", "")
    if xri:
        return xri.strip() or None
    remote = request.META.get("REMOTE_ADDR", "")
    return remote or None


__all__ = ["AuditMiddleware"]
