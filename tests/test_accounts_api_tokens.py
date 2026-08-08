"""API Token 认证机制测试.

覆盖：

- 模型：``hash_plaintext``/``generate``/``rotate``/``is_valid``/``touch_last_used``/``has_scope``。
- 认证类 ``ApiTokenAuth``：X-API-Token 头、Bearer 头、无效/吊销/过期/禁用用户/空值。
- Router ``/api/v1/tokens``：创建/列表/详情/吊销/轮换，权限校验，审计日志记录。
- 幂等主体：Token 自动作为幂等 key 主体（与 ``user:{pk}`` 隔离）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest
from apps.accounts.auth import ApiTokenAuth
from apps.accounts.jwt import create_access_token
from apps.accounts.models import ApiToken, Role, User
from apps.audit.models import AuditAction, AuditLog
from apps.system.idempotency import get_idempotent_subject
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.utils import timezone

# ================================================================
# 模型测试
# ================================================================


@pytest.mark.django_db
def test_hash_plaintext_returns_sha256_hex() -> None:
    """hash_plaintext 应返回 64 字符 SHA-256 十六进制."""
    h = ApiToken.hash_plaintext("abc123")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.django_db
def test_hash_plaintext_deterministic() -> None:
    """相同输入应产生相同哈希."""
    assert ApiToken.hash_plaintext("same") == ApiToken.hash_plaintext("same")


@pytest.mark.django_db
def test_generate_returns_plaintext_and_persists_hash(
    make_user: Callable[..., User],
) -> None:
    """generate 应返回明文并持久化哈希（DB 不存明文）."""
    user = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(
        name="ci-token",
        created_by=user,
        scopes=["datasets:read"],
    )
    assert isinstance(plaintext, str)
    assert len(plaintext) >= 32  # token_urlsafe(32) ~43 chars
    # DB 存哈希与前缀，不存明文
    assert token_obj.token_hash == ApiToken.hash_plaintext(plaintext)
    assert token_obj.prefix == plaintext[:8]
    assert token_obj.scopes == ["datasets:read"]
    assert token_obj.created_by_id == user.pk
    assert token_obj.is_active is True
    assert token_obj.expires_at is None
    # 明文不在 DB 中
    assert ApiToken.objects.filter(token_hash=plaintext).count() == 0


@pytest.mark.django_db
def test_generate_with_default_scopes_empty(make_user: Callable[..., User]) -> None:
    """scopes 为 None 时存空列表."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="empty", created_by=user)
    assert token_obj.scopes == []


@pytest.mark.django_db
def test_rotate_overwrites_hash_and_keeps_pk(
    make_user: Callable[..., User],
) -> None:
    """rotate 应覆盖哈希/前缀，保持主键与其他字段不变."""
    user = make_user(role=Role.ADMIN)
    plaintext_old, token_obj = ApiToken.generate(name="rotate-me", created_by=user, scopes=["datasets:read"])
    pk_before = token_obj.pk
    hash_before = token_obj.token_hash
    prefix_before = token_obj.prefix

    plaintext_new = token_obj.rotate()

    assert plaintext_new != plaintext_old
    token_obj.refresh_from_db()
    assert token_obj.pk == pk_before
    assert token_obj.token_hash == ApiToken.hash_plaintext(plaintext_new)
    assert token_obj.token_hash != hash_before
    assert token_obj.prefix == plaintext_new[:8]
    assert token_obj.prefix != prefix_before
    assert token_obj.is_active is True
    assert token_obj.last_used_at is None
    # 旧明文失效
    assert ApiToken.objects.filter(token_hash=ApiToken.hash_plaintext(plaintext_old)).count() == 0


@pytest.mark.django_db
def test_rotate_reactivates_revoked_token(make_user: Callable[..., User]) -> None:
    """已吊销的 Token 轮换后应重新启用."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="revoked", created_by=user)
    token_obj.is_active = False
    token_obj.save(update_fields=["is_active"])
    assert not token_obj.is_active

    token_obj.rotate()
    token_obj.refresh_from_db()
    assert token_obj.is_active is True


@pytest.mark.django_db
def test_is_valid_active_no_expiry(make_user: Callable[..., User]) -> None:
    """启用且无过期的 Token 有效."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="valid", created_by=user)
    assert token_obj.is_valid() is True


@pytest.mark.django_db
def test_is_valid_revoked(make_user: Callable[..., User]) -> None:
    """已吊销的 Token 无效."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="revoked", created_by=user)
    token_obj.is_active = False
    token_obj.save(update_fields=["is_active"])
    assert token_obj.is_valid() is False


@pytest.mark.django_db
def test_is_valid_expired(make_user: Callable[..., User]) -> None:
    """已过期的 Token 无效."""
    user = make_user(role=Role.ADMIN)
    past = timezone.now() - timedelta(seconds=1)
    _, token_obj = ApiToken.generate(name="expired", created_by=user, expires_at=past)
    assert token_obj.is_valid() is False


@pytest.mark.django_db
def test_is_valid_future_expiry(make_user: Callable[..., User]) -> None:
    """未过期的 Token 有效."""
    user = make_user(role=Role.ADMIN)
    future = timezone.now() + timedelta(days=1)
    _, token_obj = ApiToken.generate(name="future", created_by=user, expires_at=future)
    assert token_obj.is_valid() is True


@pytest.mark.django_db
def test_touch_last_used_updates_timestamp(
    make_user: Callable[..., User],
) -> None:
    """touch_last_used 应更新 last_used_at."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="touch", created_by=user)
    assert token_obj.last_used_at is None
    before = timezone.now()
    token_obj.touch_last_used()
    token_obj.refresh_from_db()
    assert token_obj.last_used_at is not None
    assert token_obj.last_used_at >= before


@pytest.mark.django_db
def test_has_scope(make_user: Callable[..., User]) -> None:
    """has_scope 检查权限范围."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="scoped", created_by=user, scopes=["datasets:read", "sync:trigger"])
    assert token_obj.has_scope("datasets:read") is True
    assert token_obj.has_scope("sync:trigger") is True
    assert token_obj.has_scope("datasets:write") is False


@pytest.mark.django_db
def test_token_str_representation(make_user: Callable[..., User]) -> None:
    """__str__ 应返回名称与前缀."""
    user = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="str-test", created_by=user)
    s = str(token_obj)
    assert "str-test" in s
    assert token_obj.prefix in s


# ================================================================
# ApiTokenAuth 认证类测试
# ================================================================


def _make_token_request(
    factory: RequestFactory,
    *,
    x_api_token: str | None = None,
    bearer: str | None = None,
) -> object:
    """构造带指定认证头的请求."""
    kwargs: dict[str, str] = {}
    if x_api_token is not None:
        kwargs["HTTP_X_API_TOKEN"] = x_api_token
    if bearer is not None:
        kwargs["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
    return factory.get("/", **kwargs)


@pytest.mark.django_db
def test_auth_x_api_token_header_valid(make_user: Callable[..., User]) -> None:
    """X-API-Token 头携带有效 token 应认证成功并挂载 api_token."""
    user = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(name="auth-x", created_by=user)
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    result = auth(cast(object, request))  # type: ignore[arg-type]
    assert result == user
    assert getattr(request, "api_token", None) == token_obj  # type: ignore[attr-defined]
    # last_used_at 被刷新
    token_obj.refresh_from_db()
    assert token_obj.last_used_at is not None


@pytest.mark.django_db
def test_auth_bearer_header_valid(make_user: Callable[..., User]) -> None:
    """Authorization: Bearer 携带有效 token 应认证成功."""
    user = make_user(role=Role.ADMIN)
    plaintext, _ = ApiToken.generate(name="auth-bearer", created_by=user)
    factory = RequestFactory()
    request = _make_token_request(factory, bearer=plaintext)
    auth = ApiTokenAuth()
    result = auth(cast(object, request))  # type: ignore[arg-type]
    assert result == user


@pytest.mark.django_db
def test_auth_x_api_token_takes_priority_over_bearer(
    make_user: Callable[..., User],
) -> None:
    """同时携带两头时优先用 X-API-Token（即使 Bearer 无效）."""
    user = make_user(role=Role.ADMIN)
    plaintext, _ = ApiToken.generate(name="priority", created_by=user)
    factory = RequestFactory()
    # X-API-Token 有效，Bearer 是垃圾值
    request = _make_token_request(factory, x_api_token=plaintext, bearer="garbage")
    auth = ApiTokenAuth()
    result = auth(cast(object, request))  # type: ignore[arg-type]
    assert result == user


@pytest.mark.django_db
def test_auth_invalid_token_returns_none() -> None:
    """无效 token 返回 None."""
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token="nonexistent-token")
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_no_header_returns_none() -> None:
    """无认证头返回 None."""
    factory = RequestFactory()
    request = _make_token_request(factory)
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_revoked_token_returns_none(make_user: Callable[..., User]) -> None:
    """已吊销的 token 返回 None."""
    user = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(name="revoked", created_by=user)
    token_obj.is_active = False
    token_obj.save(update_fields=["is_active"])
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_expired_token_returns_none(make_user: Callable[..., User]) -> None:
    """已过期的 token 返回 None."""
    user = make_user(role=Role.ADMIN)
    past = timezone.now() - timedelta(seconds=1)
    plaintext, _ = ApiToken.generate(name="expired", created_by=user, expires_at=past)
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_inactive_user_returns_none(make_user: Callable[..., User]) -> None:
    """Token 创建者被禁用时返回 None."""
    user = make_user(role=Role.ADMIN, is_active=False)
    plaintext, _ = ApiToken.generate(name="inactive-user", created_by=user)
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_empty_x_api_token_returns_none() -> None:
    """空字符串 X-API-Token 返回 None（不回退到 Bearer）."""
    factory = RequestFactory()
    # X-API-Token 为空字符串，Bearer 不携带
    request = _make_token_request(factory, x_api_token="")
    auth = ApiTokenAuth()
    # 空字符串为 falsy，回退到 super().__call__，无 Authorization 头返回 None
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
def test_auth_whitespace_token_stripped_and_rejected(
    make_user: Callable[..., User],
) -> None:
    """纯空白 token strip 后为空，返回 None."""
    user = make_user(role=Role.ADMIN)
    plaintext, _ = ApiToken.generate(name="ws", created_by=user)
    factory = RequestFactory()
    # 携带有效 token 但前后空白
    request = _make_token_request(factory, x_api_token=f"  {plaintext}  ")
    auth = ApiTokenAuth()
    result = auth(cast(object, request))  # type: ignore[arg-type]
    assert result == user  # strip 后匹配


@pytest.mark.django_db
def test_auth_does_not_fallback_on_invalid_x_token(
    make_user: Callable[..., User],
) -> None:
    """X-API-Token 存在但无效时不回退到 Bearer（避免认证绕过）."""
    user = make_user(role=Role.ADMIN)
    plaintext, _ = ApiToken.generate(name="no-fallback", created_by=user)
    factory = RequestFactory()
    # X-API-Token 是无效值，Bearer 是有效 token
    request = _make_token_request(factory, x_api_token="invalid", bearer=plaintext)
    auth = ApiTokenAuth()
    # X-API-Token 非空但认证失败，应返回 None（不回退到 Bearer）
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]


# ================================================================
# /api/v1/tokens Router 测试
# ================================================================


def _post(client: Client, url: str, body: dict[str, object] | None = None) -> HttpResponse:
    """发送 POST 请求."""
    if body is None:
        return cast(HttpResponse, client.post(url))
    return cast(HttpResponse, client.post(url, data=json.dumps(body), content_type="application/json"))


def _get(client: Client, url: str) -> HttpResponse:
    """发送 GET 请求."""
    return cast(HttpResponse, client.get(url))


def _auth_client(user: User) -> Client:
    """构造携带 JWT 的客户端（默认头方式）."""
    token = create_access_token(user.pk, str(user.role))
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
def test_create_token_returns_plaintext_once(
    make_user: Callable[..., User],
) -> None:
    """管理员创建 Token 应返回明文（仅此一次）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    client = _auth_client(admin)
    response = _post(
        client,
        "/api/v1/tokens",
        {"name": "ci", "scopes": ["datasets:read"]},
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["name"] == "ci"
    assert "token" in body
    assert len(body["token"]) >= 32
    assert body["prefix"] == body["token"][:8]
    assert body["scopes"] == ["datasets:read"]
    assert body["is_active"] is True
    # DB 中不存明文
    assert ApiToken.objects.filter(name="ci").count() == 1
    obj = ApiToken.objects.get(name="ci")
    assert obj.token_hash == ApiToken.hash_plaintext(body["token"])
    # 审计日志记录
    assert AuditLog.objects.filter(action=AuditAction.TOKEN_CREATE, resource_id=str(obj.pk)).exists()


@pytest.mark.django_db
def test_create_token_with_expiry(make_user: Callable[..., User]) -> None:
    """创建带过期时间的 Token."""
    admin = make_user(role=Role.ADMIN)
    client = _auth_client(admin)
    future = (timezone.now() + timedelta(days=30)).isoformat()
    response = _post(
        client,
        "/api/v1/tokens",
        {"name": "exp", "scopes": [], "expires_at": future},
    )
    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["expires_at"] is not None


@pytest.mark.django_db
def test_create_token_denied_for_non_admin(
    make_user: Callable[..., User],
) -> None:
    """非管理员创建 Token 返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    client = _auth_client(viewer)
    response = _post(client, "/api/v1/tokens", {"name": "forbidden"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_create_token_requires_auth() -> None:
    """未认证创建 Token 返回 401."""
    client = Client()
    response = _post(client, "/api/v1/tokens", {"name": "noauth"})
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_tokens(make_user: Callable[..., User]) -> None:
    """管理员列表返回所有 Token（不含明文）."""
    admin = make_user(role=Role.ADMIN)
    ApiToken.generate(name="t1", created_by=admin)
    ApiToken.generate(name="t2", created_by=admin, scopes=["datasets:read"])
    client = _auth_client(admin)
    response = _get(client, "/api/v1/tokens")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"t1", "t2"}
    # 不含明文
    for item in body["items"]:
        assert "token" not in item
        assert "prefix" in item
        assert "scopes" in item


@pytest.mark.django_db
def test_list_tokens_denied_for_non_admin(
    make_user: Callable[..., User],
) -> None:
    """非管理员列表返回 403."""
    viewer = make_user(username="viewer", role=Role.VIEWER)
    client = _auth_client(viewer)
    response = _get(client, "/api/v1/tokens")
    assert response.status_code == 403


@pytest.mark.django_db
def test_retrieve_token(make_user: Callable[..., User]) -> None:
    """管理员获取单个 Token 详情."""
    admin = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="detail", created_by=admin)
    client = _auth_client(admin)
    response = _get(client, f"/api/v1/tokens/{token_obj.pk}")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["id"] == token_obj.pk
    assert body["name"] == "detail"
    assert "token" not in body


@pytest.mark.django_db
def test_retrieve_token_not_found(make_user: Callable[..., User]) -> None:
    """不存在的 Token ID 返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = _auth_client(admin)
    response = _get(client, "/api/v1/tokens/99999")
    assert response.status_code == 404


@pytest.mark.django_db
def test_revoke_token(make_user: Callable[..., User]) -> None:
    """吊销 Token 后 is_active=False，审计日志记录."""
    admin = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(name="revoke", created_by=admin)
    client = _auth_client(admin)
    response = _post(client, f"/api/v1/tokens/{token_obj.pk}/revoke")
    assert response.status_code == 200
    token_obj.refresh_from_db()
    assert token_obj.is_active is False
    # 旧明文失效
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    assert auth(cast(object, request)) is None  # type: ignore[arg-type]
    # 审计日志
    assert AuditLog.objects.filter(action=AuditAction.TOKEN_REVOKE, resource_id=str(token_obj.pk)).exists()


@pytest.mark.django_db
def test_revoke_token_twice_returns_400(
    make_user: Callable[..., User],
) -> None:
    """重复吊销已吊销的 Token 返回 400."""
    admin = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="twice", created_by=admin)
    client = _auth_client(admin)
    response1 = _post(client, f"/api/v1/tokens/{token_obj.pk}/revoke")
    assert response1.status_code == 200
    response2 = _post(client, f"/api/v1/tokens/{token_obj.pk}/revoke")
    assert response2.status_code == 400


@pytest.mark.django_db
def test_rotate_token(make_user: Callable[..., User]) -> None:
    """轮换 Token 后旧明文失效、新明文有效，审计日志记录."""
    admin = make_user(role=Role.ADMIN)
    plaintext_old, token_obj = ApiToken.generate(name="rotate", created_by=admin)
    client = _auth_client(admin)
    response = _post(client, f"/api/v1/tokens/{token_obj.pk}/rotate")
    assert response.status_code == 200
    body = json.loads(response.content)
    plaintext_new = body["token"]
    assert plaintext_new != plaintext_old
    assert body["prefix"] == plaintext_new[:8]
    assert body["is_active"] is True

    # 旧明文失效
    factory = RequestFactory()
    request_old = _make_token_request(factory, x_api_token=plaintext_old)
    auth = ApiTokenAuth()
    assert auth(cast(object, request_old)) is None  # type: ignore[arg-type]
    # 新明文有效
    request_new = _make_token_request(factory, x_api_token=plaintext_new)
    assert auth(cast(object, request_new)) == admin  # type: ignore[arg-type]
    # 审计日志
    assert AuditLog.objects.filter(action=AuditAction.TOKEN_ROTATE, resource_id=str(token_obj.pk)).exists()


@pytest.mark.django_db
def test_rotate_revoked_token_reactivates(
    make_user: Callable[..., User],
) -> None:
    """轮换已吊销的 Token 后重新启用."""
    admin = make_user(role=Role.ADMIN)
    _, token_obj = ApiToken.generate(name="reactivate", created_by=admin)
    token_obj.is_active = False
    token_obj.save(update_fields=["is_active"])
    client = _auth_client(admin)
    response = _post(client, f"/api/v1/tokens/{token_obj.pk}/rotate")
    assert response.status_code == 200
    token_obj.refresh_from_db()
    assert token_obj.is_active is True


@pytest.mark.django_db
def test_rotate_token_not_found(make_user: Callable[..., User]) -> None:
    """轮换不存在的 Token 返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = _auth_client(admin)
    response = _post(client, "/api/v1/tokens/99999/rotate")
    assert response.status_code == 404


@pytest.mark.django_db
def test_admin_registers_api_token_model() -> None:
    """ApiToken 应注册到 admin.site."""
    from django.contrib import admin

    assert ApiToken in admin.site._registry


# ================================================================
# 幂等主体集成测试
# ================================================================


@pytest.mark.django_db
def test_idempotent_subject_with_token(make_user: Callable[..., User]) -> None:
    """Token 认证后幂等主体应为 token:{prefix}."""
    user = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(name="idem", created_by=user)
    factory = RequestFactory()
    request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    auth(cast(object, request))  # type: ignore[arg-type]
    subject = get_idempotent_subject(cast(object, request))  # type: ignore[arg-type]
    assert subject == f"token:{token_obj.prefix}"


@pytest.mark.django_db
def test_idempotent_subject_token_isolates_from_user(
    make_user: Callable[..., User],
) -> None:
    """Token 主体与 user 主体隔离：相同 key 不同主体不冲突."""
    user = make_user(role=Role.ADMIN)
    plaintext, token_obj = ApiToken.generate(name="iso", created_by=user)
    factory = RequestFactory()

    # Token 请求
    token_request = _make_token_request(factory, x_api_token=plaintext)
    auth = ApiTokenAuth()
    auth(cast(object, token_request))  # type: ignore[arg-type]
    token_subject = get_idempotent_subject(cast(object, token_request))  # type: ignore[arg-type]

    # JWT 请求（mock）
    jwt_request = MagicMock()
    jwt_user = MagicMock()
    jwt_user.pk = user.pk
    jwt_request.auth = jwt_user
    jwt_request.headers = {}
    user_subject = get_idempotent_subject(jwt_request)

    assert token_subject == f"token:{token_obj.prefix}"
    assert user_subject == f"user:{user.pk}"
    assert token_subject != user_subject
