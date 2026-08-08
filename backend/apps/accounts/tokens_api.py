"""API Token 管理 Router（管理员独占）.

提供 Token 的创建、列表、详情、吊销、轮换接口，全部要求 admin 角色。
通过 Router 级别 ``auth=JWTAuth()`` 统一认证，每个路由体首行调用
``require_admin(request)`` 校验权限。

设计要点：

- 创建与轮换响应包含明文 token，**仅此一次返回**，调用方应立即交付给用户；
  列表与详情不返回明文，仅返回 ``prefix``（前 8 位）用于展示识别。
- 吊销将 ``is_active`` 置 False，DB 记录保留用于审计；轮换生成新明文并
  覆盖哈希，旧明文立即失效。
- 所有关键操作通过 ``log_audit`` 记录业务审计日志（含 Token ID 与 prefix）。
"""

from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.audit.audit import log_audit
from apps.audit.models import AuditAction

from .auth import JWTAuth
from .models import ApiToken, User
from .permissions import require_admin
from .schemas import (
    ApiTokenCreateIn,
    ApiTokenListItemOut,
    ApiTokenListOut,
    ApiTokenOut,
    ApiTokenRotateOut,
    MessageOut,
)

router = Router(tags=["tokens"], auth=JWTAuth())


def _token_to_out(token_obj: ApiToken, plaintext: str) -> dict[str, Any]:
    """构造含明文的 Token 响应字典（创建/轮换时使用）."""
    return ApiTokenOut(
        id=token_obj.pk,
        name=token_obj.name,
        token=plaintext,
        prefix=token_obj.prefix,
        scopes=list(token_obj.scopes),
        expires_at=token_obj.expires_at,
        is_active=token_obj.is_active,
        created_at=token_obj.created_at,
    ).model_dump()


def _token_to_list_item(token_obj: ApiToken) -> dict[str, Any]:
    """构造 Token 列表项字典（不含明文）."""
    return ApiTokenListItemOut(
        id=token_obj.pk,
        name=token_obj.name,
        prefix=token_obj.prefix,
        scopes=list(token_obj.scopes),
        expires_at=token_obj.expires_at,
        last_used_at=token_obj.last_used_at,
        is_active=token_obj.is_active,
        created_by_id=token_obj.created_by_id,
        created_at=token_obj.created_at,
    ).model_dump()


def _get_token_or_404(token_id: int) -> ApiToken:
    """按主键获取 Token，不存在抛 404."""
    try:
        return ApiToken.objects.get(pk=token_id)
    except ApiToken.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"Token {token_id} 不存在") from None


@router.get("", response={200: ApiTokenListOut})
def list_tokens(request: HttpRequest) -> HttpResponse:
    """列出全部 API Token（仅管理员）.

    按创建时间倒序返回，不含明文。支持按 ``is_active`` 与 ``created_by_id`` 过滤。
    """
    require_admin(request)
    qs = ApiToken.objects.all().order_by("-id")
    items = [_token_to_list_item(t) for t in qs]
    body = ApiTokenListOut(items=items, total=len(items)).model_dump()
    return JsonResponse(body)


@router.post("", response={201: ApiTokenOut})
def create_token(request: HttpRequest, payload: ApiTokenCreateIn) -> HttpResponse:
    """创建 API Token（仅管理员）.

    生成 URL 安全的随机明文，DB 仅存 SHA-256 哈希与前 8 位前缀。
    明文 **仅此一次返回**，调用方应立即保存。
    """
    require_admin(request)
    user = cast(User, getattr(request, "auth", None))
    plaintext, token_obj = ApiToken.generate(
        name=payload.name,
        created_by=user,
        scopes=list(payload.scopes),
        expires_at=payload.expires_at,
    )
    log_audit(
        request,
        action=AuditAction.TOKEN_CREATE,
        resource_type="api_token",
        resource_id=str(token_obj.pk),
        extra={"name": token_obj.name, "prefix": token_obj.prefix, "scopes": list(token_obj.scopes)},
    )
    body = _token_to_out(token_obj, plaintext)
    return JsonResponse(body, status=201)


@router.get("/{token_id}", response={200: ApiTokenListItemOut})
def retrieve_token(request: HttpRequest, token_id: int) -> HttpResponse:
    """获取单个 API Token 详情（仅管理员，不含明文）."""
    require_admin(request)
    token_obj = _get_token_or_404(token_id)
    return JsonResponse(_token_to_list_item(token_obj))


@router.post("/{token_id}/revoke", response={200: MessageOut})
def revoke_token(request: HttpRequest, token_id: int) -> HttpResponse:
    """吊销 API Token（仅管理员）.

    将 ``is_active`` 置 False，DB 记录保留用于审计。已吊销的 Token 再次吊销返回 400。
    """
    require_admin(request)
    token_obj = _get_token_or_404(token_id)
    if not token_obj.is_active:
        raise HttpError(400, "Token 已吊销")
    token_obj.is_active = False
    token_obj.save(update_fields=["is_active"])
    log_audit(
        request,
        action=AuditAction.TOKEN_REVOKE,
        resource_type="api_token",
        resource_id=str(token_obj.pk),
        extra={"name": token_obj.name, "prefix": token_obj.prefix},
    )
    return JsonResponse(MessageOut(detail=f"Token {token_obj.prefix}... 已吊销").model_dump())


@router.post("/{token_id}/rotate", response={200: ApiTokenRotateOut})
def rotate_token(request: HttpRequest, token_id: int) -> HttpResponse:
    """轮换 API Token（仅管理员）.

    生成新明文并覆盖哈希/前缀，旧明文立即失效。新明文 **仅此一次返回**。
    已吊销的 Token 轮换后自动重新启用（``is_active=True``）。
    """
    require_admin(request)
    token_obj = _get_token_or_404(token_id)
    old_prefix = token_obj.prefix
    plaintext = token_obj.rotate()
    log_audit(
        request,
        action=AuditAction.TOKEN_ROTATE,
        resource_type="api_token",
        resource_id=str(token_obj.pk),
        extra={"name": token_obj.name, "old_prefix": old_prefix, "new_prefix": token_obj.prefix},
    )
    body = ApiTokenRotateOut(
        id=token_obj.pk,
        name=token_obj.name,
        token=plaintext,
        prefix=token_obj.prefix,
        is_active=token_obj.is_active,
    ).model_dump()
    return JsonResponse(body)


__all__ = ["router"]
