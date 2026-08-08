"""审计日志中间件测试.

验证：

- 写操作（POST/PATCH/PUT/DELETE）触发记录
- GET/HEAD/OPTIONS 不记录
- 排除路径（/health、/api/v1/docs 等）不记录
- 失败状态码记录为 failure
- IP 提取优先级（X-Forwarded-For → X-Real-IP → REMOTE_ADDR）
- 中间件异常不阻断业务
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.audit.middleware import (
    AuditMiddleware,
    _get_client_ip,
    _is_excluded,
)
from apps.audit.models import AuditAction, AuditLog, AuditSource, AuditStatus
from django.http import HttpRequest, HttpResponse
from django.test import Client


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# ---------- _is_excluded 单测 ----------


def test_is_excluded_health() -> None:
    """/health 与 /health/ 都应被排除."""
    assert _is_excluded("/health") is True
    assert _is_excluded("/health/") is True


def test_is_excluded_docs() -> None:
    """/api/v1/docs 与 /api/v1/openapi 应被排除."""
    assert _is_excluded("/api/v1/docs") is True
    assert _is_excluded("/api/v1/openapi") is True


def test_is_excluded_static() -> None:
    """/static/ 前缀应被排除."""
    assert _is_excluded("/static/js/main.js") is True


def test_is_excluded_admin_jsi18n() -> None:
    """/admin/jsi18n/ 应被排除."""
    assert _is_excluded("/admin/jsi18n/") is True


def test_is_excluded_normal_path() -> None:
    """普通业务路径不排除."""
    assert _is_excluded("/api/v1/datasources") is False
    assert _is_excluded("/api/v1/audit/logs") is False


# ---------- _get_client_ip 单测 ----------


def _make_request(meta: dict[str, str]) -> HttpRequest:
    """构造带 META 的 HttpRequest."""
    req = HttpRequest()
    req.META = meta  # type: ignore[assignment]
    return req


def test_get_client_ip_from_xff() -> None:
    """X-Forwarded-For 存在时取第一个 IP."""
    req = _make_request({"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8", "REMOTE_ADDR": "9.9.9.9"})
    assert _get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_from_xff_with_spaces() -> None:
    """X-Forwarded-For 含空格时应正确 trim."""
    req = _make_request({"HTTP_X_FORWARDED_FOR": " 1.2.3.4 , 5.6.7.8 "})
    assert _get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_from_xreal_ip() -> None:
    """无 X-Forwarded-For 时使用 X-Real-IP."""
    req = _make_request({"HTTP_X_REAL_IP": "5.6.7.8", "REMOTE_ADDR": "9.9.9.9"})
    assert _get_client_ip(req) == "5.6.7.8"


def test_get_client_ip_from_remote_addr() -> None:
    """无代理头时回退到 REMOTE_ADDR."""
    req = _make_request({"REMOTE_ADDR": "9.9.9.9"})
    assert _get_client_ip(req) == "9.9.9.9"


def test_get_client_ip_empty_returns_none() -> None:
    """全部为空时返回 None."""
    req = _make_request({})
    assert _get_client_ip(req) is None


# ---------- 中间件集成（通过 HTTP Client） ----------


@pytest.mark.django_db
def test_post_triggers_audit_log(make_user: Callable[..., User]) -> None:
    """POST 请求应触发中间件记录一条 source=MIDDLEWARE 的审计日志."""
    admin = make_user(role=Role.ADMIN)
    before = AuditLog.objects.count()
    client = Client()
    resp = client.post(
        "/api/v1/datasources",
        data='{"name":"mw-test","engine":"sqlite","database":":memory:"}',
        content_type="application/json",
        **_auth(admin),
    )
    assert resp.status_code == 201  # type: ignore[missing-attribute]
    after = AuditLog.objects.count()
    # 中间件记录 + 业务层 log_audit 各一条
    assert after - before >= 1
    mw_log = AuditLog.objects.filter(
        source=AuditSource.MIDDLEWARE,
        path="/api/v1/datasources",
        method="POST",
    ).first()
    assert mw_log is not None
    assert mw_log.action == AuditAction.WRITE
    assert mw_log.status == AuditStatus.SUCCESS
    assert mw_log.username == admin.username
    assert mw_log.user_id == admin.pk
    assert mw_log.elapsed_ms is not None and mw_log.elapsed_ms >= 0


@pytest.mark.django_db
def test_get_does_not_trigger_audit(make_user: Callable[..., User]) -> None:
    """GET 请求不应触发中间件记录审计日志."""
    user = make_user(role=Role.VIEWER)
    before = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    client = Client()
    resp = client.get("/api/v1/datasources", **_auth(user))
    assert resp.status_code == 200  # type: ignore[missing-attribute]
    after = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    assert after == before


@pytest.mark.django_db
def test_delete_triggers_audit(make_user: Callable[..., User]) -> None:
    """DELETE 请求应触发中间件记录."""
    admin = make_user(role=Role.ADMIN)
    # 先创建一个数据源（会记录 2 条审计），再删除
    from apps.datasources.models import DataSource, EngineType

    ds = DataSource.objects.create(name="del-test", engine=EngineType.SQLITE, database=":memory:")
    client = Client()
    before = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE, method="DELETE").count()
    resp = client.delete(f"/api/v1/datasources/{ds.pk}", **_auth(admin))
    assert resp.status_code == 200  # type: ignore[missing-attribute]
    after = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE, method="DELETE").count()
    assert after - before == 1


@pytest.mark.django_db
def test_failure_status_recorded_as_failure(make_user: Callable[..., User]) -> None:
    """返回 4xx/5xx 状态码的写操作应记录 status=FAILURE."""
    viewer = make_user(role=Role.VIEWER)
    before = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE, status=AuditStatus.FAILURE).count()
    client = Client()
    # viewer 创建会返回 403
    resp = client.post(
        "/api/v1/datasources",
        data='{"name":"fail-test","engine":"sqlite","database":":memory:"}',
        content_type="application/json",
        **_auth(viewer),
    )
    assert resp.status_code == 403  # type: ignore[missing-attribute]
    after = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE, status=AuditStatus.FAILURE).count()
    assert after - before == 1
    log = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE, status=AuditStatus.FAILURE).order_by("-id").first()
    assert log is not None
    assert "403" in log.error_message


@pytest.mark.django_db
def test_excluded_path_not_audited() -> None:
    """排除路径（/health）不应触发中间件记录."""
    before = AuditLog.objects.count()
    client = Client()
    client.get("/health")
    after = AuditLog.objects.count()
    assert after == before


@pytest.mark.django_db
def test_unauthenticated_write_still_audited(make_user: Callable[..., User]) -> None:
    """未认证的写请求也应记录审计日志（user 为空）."""
    before = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    client = Client()
    # 未带 token 的 POST 会返回 401，但仍触发中间件记录
    resp = client.post(
        "/api/v1/datasources",
        data='{"name":"noauth","engine":"sqlite","database":":memory:"}',
        content_type="application/json",
    )
    assert resp.status_code == 401  # type: ignore[missing-attribute]
    after = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).count()
    assert after - before == 1
    log = AuditLog.objects.filter(source=AuditSource.MIDDLEWARE).order_by("-id").first()
    assert log is not None
    assert log.user_id is None
    assert log.username == ""


# ---------- 中间件异常不阻断业务 ----------


def test_middleware_exception_does_not_block_response() -> None:
    """中间件记录失败时不应影响业务响应（异常被捕获）."""
    captured: list[str] = []

    def fake_get_response(request: HttpRequest) -> HttpResponse:
        return HttpResponse("ok", status=200)

    mw = AuditMiddleware(fake_get_response)

    # 构造一个会触发 _record_middleware_audit 异常的请求（method=POST）
    request = HttpRequest()
    request.method = "POST"
    request.path = "/api/v1/test"
    request.META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "test"}  # type: ignore[assignment]
    # monkeypatch AuditLog.objects.create_with_hash 抛异常
    import apps.audit.middleware as mw_module

    original_create = mw_module.AuditLog.objects.create_with_hash

    def boom(*_args: object, **_kwargs: object) -> AuditLog:
        raise RuntimeError("db down")

    mw_module.AuditLog.objects.create_with_hash = boom  # type: ignore[assignment]
    try:
        resp = mw(request)
        assert resp.status_code == 200
        assert resp.content == b"ok"
        captured.append("ok")
    finally:
        mw_module.AuditLog.objects.create_with_hash = original_create  # type: ignore[assignment]
    assert captured == ["ok"]


# ---------- log_audit 辅助函数 ----------


from apps.audit.audit import _get_client_ip as _audit_get_client_ip  # noqa: E402
from apps.audit.audit import log_audit  # noqa: E402


def test_audit_get_client_ip_from_xff() -> None:
    """log_audit 内 _get_client_ip 从 X-Forwarded-For 取首个 IP."""
    req = _make_request({"HTTP_X_FORWARDED_FOR": "1.1.1.1, 2.2.2.2"})
    assert _audit_get_client_ip(req) == "1.1.1.1"


def test_audit_get_client_ip_from_xreal_ip() -> None:
    """log_audit 内 _get_client_ip 在无 XFF 时回退到 X-Real-IP."""
    req = _make_request({"HTTP_X_REAL_IP": "3.3.3.3"})
    assert _audit_get_client_ip(req) == "3.3.3.3"


def test_audit_get_client_ip_from_remote_addr() -> None:
    """log_audit 内 _get_client_ip 在无代理头时回退到 REMOTE_ADDR."""
    req = _make_request({"REMOTE_ADDR": "4.4.4.4"})
    assert _audit_get_client_ip(req) == "4.4.4.4"


def test_audit_get_client_ip_empty_returns_none() -> None:
    """log_audit 内 _get_client_ip 在全部为空时返回 None."""
    req = _make_request({})
    assert _audit_get_client_ip(req) is None


@pytest.mark.django_db
def test_log_audit_returns_audit_log(make_user: Callable[..., User]) -> None:
    """log_audit 正常调用应返回创建的 AuditLog 实例."""
    admin = make_user(role=Role.ADMIN)
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {"REMOTE_ADDR": "1.2.3.4", "HTTP_USER_AGENT": "pytest"}  # type: ignore[assignment]
    req.auth = admin  # type: ignore[missing-attribute]
    log = log_audit(
        req,
        action=AuditAction.DML_INSERT,
        resource_type="row",
        resource_id="1",
        sql="INSERT INTO t VALUES(1)",
        row_count=1,
    )
    assert log is not None
    assert log.action == AuditAction.DML_INSERT
    assert log.source == AuditSource.BUSINESS
    assert log.username == admin.username
    assert log.sql == "INSERT INTO t VALUES(1)"
    assert log.ip == "1.2.3.4"


@pytest.mark.django_db
def test_log_audit_truncates_long_sql() -> None:
    """log_audit 应将超长 SQL 截断到 _MAX_SQL_LENGTH."""
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {}  # type: ignore[assignment]
    long_sql = "SELECT " + "a, " * 5000 + "a"
    log = log_audit(req, sql=long_sql)
    assert log is not None
    from apps.audit.audit import _MAX_SQL_LENGTH

    assert len(log.sql) == _MAX_SQL_LENGTH  # type: ignore[bad-argument-type]


@pytest.mark.django_db
def test_log_audit_exception_returns_none() -> None:
    """log_audit 在 AuditLog.objects.create_with_hash 失败时应返回 None（不抛异常）."""
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {}  # type: ignore[assignment]
    import apps.audit.audit as audit_module

    original_create = audit_module.AuditLog.objects.create_with_hash

    def boom(*_args: object, **_kwargs: object) -> AuditLog:
        raise RuntimeError("db down")

    audit_module.AuditLog.objects.create_with_hash = boom  # type: ignore[assignment]
    try:
        result = log_audit(req, action=AuditAction.WRITE)
        assert result is None
    finally:
        audit_module.AuditLog.objects.create_with_hash = original_create  # type: ignore[assignment]


@pytest.mark.django_db
def test_log_audit_with_extra_none() -> None:
    """log_audit 传入 extra=None 时应存入空 dict."""
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {}  # type: ignore[assignment]
    log = log_audit(req, extra=None)
    assert log is not None
    assert log.extra == {}
