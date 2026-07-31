"""审计日志查询与导出 API 测试.

覆盖：

- list 分页与筛选（user_id/username/action/source/status/resource_type/datasource_id/path/start/end）
- retrieve 详情（存在/不存在）
- export CSV 导出（含 BOM、流式响应、文件名）
- 权限：admin 可访问、非 admin 403、未认证 401
- 参数校验：page<1 / page_size<1 返回 400
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from typing import cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.audit.models import (
    AuditAction,
    AuditLog,
    AuditSource,
    AuditStatus,
)
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _get(client: Client, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """发送 GET 请求."""
    h = headers or {}
    return cast(HttpResponse, client.get(url, **h))


def _seed_log(**kwargs: object) -> AuditLog:
    """创建一条审计日志（默认 SUCCESS/MIDDLEWARE）."""
    defaults: dict[str, object] = {
        "username": "alice",
        "action": AuditAction.DML_INSERT,
        "source": AuditSource.BUSINESS,
        "status": AuditStatus.SUCCESS,
        "method": "POST",
        "path": "/api/v1/manager/ds/1/rows",
        "resource_type": "row",
        "resource_id": "1",
        "datasource_id": 1,
        "datasource_name": "test-ds",
        "sql": "INSERT INTO t VALUES(1)",
        "row_count": 1,
        "elapsed_ms": 10,
        "ip": "127.0.0.1",
        "user_agent": "pytest",
    }
    defaults.update(kwargs)
    return AuditLog.objects.create(**defaults)  # type: ignore[arg-type]


# ---------- 权限 ----------


@pytest.mark.django_db
def test_list_without_token_returns_401() -> None:
    """未认证访问列表应返回 401."""
    client = Client()
    response = _get(client, "/api/v1/audit/logs")
    assert response.status_code == 401


@pytest.mark.django_db
def test_list_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 访问审计日志应返回 403."""
    user = make_user(role=Role.VIEWER)
    client = Client()
    response = _get(client, "/api/v1/audit/logs", _auth(user))
    assert response.status_code == 403


@pytest.mark.django_db
def test_list_by_designer_returns_403(make_user: Callable[..., User]) -> None:
    """designer 访问审计日志应返回 403."""
    user = make_user(role=Role.DESIGNER)
    client = Client()
    response = _get(client, "/api/v1/audit/logs", _auth(user))
    assert response.status_code == 403


@pytest.mark.django_db
def test_list_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """admin 访问审计日志应返回 200."""
    admin = make_user(role=Role.ADMIN)
    _seed_log()
    client = Client()
    response = _get(client, "/api/v1/audit/logs", _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["page"] == 1
    assert body["page_size"] == 20


@pytest.mark.django_db
def test_retrieve_by_admin_succeeds(make_user: Callable[..., User]) -> None:
    """admin 获取详情应返回完整字段."""
    admin = make_user(role=Role.ADMIN)
    log = _seed_log()
    client = Client()
    response = _get(client, f"/api/v1/audit/logs/{log.pk}", _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["id"] == log.pk
    assert body["username"] == "alice"
    assert body["action"] == "dml.insert"
    assert body["source"] == "business"
    assert body["sql"] == "INSERT INTO t VALUES(1)"
    assert body["row_count"] == 1


@pytest.mark.django_db
def test_retrieve_unknown_returns_404(make_user: Callable[..., User]) -> None:
    """不存在的审计日志 ID 应返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _get(client, "/api/v1/audit/logs/99999", _auth(admin))
    assert response.status_code == 404


@pytest.mark.django_db
def test_export_by_viewer_returns_403(make_user: Callable[..., User]) -> None:
    """viewer 导出审计日志应返回 403."""
    user = make_user(role=Role.VIEWER)
    client = Client()
    response = _get(client, "/api/v1/audit/logs/export", _auth(user))
    assert response.status_code == 403


# ---------- 列表分页与字段 ----------


@pytest.mark.django_db
def test_list_pagination(make_user: Callable[..., User]) -> None:
    """分页参数 page/page_size 应正确生效."""
    admin = make_user(role=Role.ADMIN)
    for _ in range(5):
        _seed_log()
    client = Client()
    response = _get(client, "/api/v1/audit/logs?page=1&page_size=2", _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2

    response2 = _get(client, "/api/v1/audit/logs?page=3&page_size=2", _auth(admin))
    body2 = json.loads(response2.content)
    assert len(body2["items"]) == 1


@pytest.mark.django_db
def test_list_page_size_capped_to_200(make_user: Callable[..., User]) -> None:
    """page_size 超过 200 应被截断到 200."""
    admin = make_user(role=Role.ADMIN)
    _seed_log()
    client = Client()
    response = _get(client, "/api/v1/audit/logs?page_size=99999", _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["page_size"] == 200


@pytest.mark.django_db
def test_list_invalid_page_returns_400(make_user: Callable[..., User]) -> None:
    """page<1 应返回 400."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?page=0", _auth(admin))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_invalid_page_size_returns_400(make_user: Callable[..., User]) -> None:
    """page_size<1 应返回 400."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?page_size=0", _auth(admin))
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_ordering_desc_by_id(make_user: Callable[..., User]) -> None:
    """列表默认按 id 降序（最新在前）."""
    admin = make_user(role=Role.ADMIN)
    first = _seed_log(sql="first")
    second = _seed_log(sql="second")
    client = Client()
    response = _get(client, "/api/v1/audit/logs", _auth(admin))
    body = json.loads(response.content)
    ids = [item["id"] for item in body["items"]]
    assert ids == [second.pk, first.pk]


@pytest.mark.django_db
def test_list_response_fields_complete(make_user: Callable[..., User]) -> None:
    """列表项应包含全部 AuditLogOut 字段."""
    admin = make_user(role=Role.ADMIN)
    log = _seed_log(
        extra={"file": "a.csv"},
        error_message="some error",
    )
    client = Client()
    response = _get(client, "/api/v1/audit/logs", _auth(admin))
    body = json.loads(response.content)
    item = body["items"][0]
    expected_keys = {
        "id",
        "user_id",
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
        "extra",
        "created_at",
    }
    assert set(item.keys()) == expected_keys
    assert item["id"] == log.pk


# ---------- 筛选 ----------


@pytest.mark.django_db
def test_filter_by_username(make_user: Callable[..., User]) -> None:
    """按用户名模糊匹配筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(username="alice")
    _seed_log(username="bob")
    _seed_log(username="alicia")
    client = Client()
    response = _get(client, "/api/v1/audit/logs?username=ali", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 2
    names = {item["username"] for item in body["items"]}
    assert names == {"alice", "alicia"}


@pytest.mark.django_db
def test_filter_by_action(make_user: Callable[..., User]) -> None:
    """按 action 精确匹配筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(action=AuditAction.DML_INSERT)
    _seed_log(action=AuditAction.DML_UPDATE)
    _seed_log(action=AuditAction.DML_DELETE)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?action=dml.update", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["action"] == "dml.update"


@pytest.mark.django_db
def test_filter_by_source(make_user: Callable[..., User]) -> None:
    """按 source 筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(source=AuditSource.MIDDLEWARE)
    _seed_log(source=AuditSource.BUSINESS)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?source=middleware", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["source"] == "middleware"


@pytest.mark.django_db
def test_filter_by_status(make_user: Callable[..., User]) -> None:
    """按 status 筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(status=AuditStatus.SUCCESS)
    _seed_log(status=AuditStatus.FAILURE)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?status=failure", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["status"] == "failure"


@pytest.mark.django_db
def test_filter_by_resource_type(make_user: Callable[..., User]) -> None:
    """按 resource_type 模糊匹配筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(resource_type="datasource")
    _seed_log(resource_type="row")
    _seed_log(resource_type="view")
    client = Client()
    response = _get(client, "/api/v1/audit/logs?resource_type=row", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1


@pytest.mark.django_db
def test_filter_by_datasource_id(make_user: Callable[..., User]) -> None:
    """按 datasource_id 精确筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(datasource_id=1)
    _seed_log(datasource_id=2)
    _seed_log(datasource_id=None)
    client = Client()
    response = _get(client, "/api/v1/audit/logs?datasource_id=2", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["datasource_id"] == 2


@pytest.mark.django_db
def test_filter_by_path(make_user: Callable[..., User]) -> None:
    """按 path 模糊匹配筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(path="/api/v1/datasources")
    _seed_log(path="/api/v1/manager/ds/1/rows")
    client = Client()
    response = _get(client, "/api/v1/audit/logs?path=datasources", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1


@pytest.mark.django_db
def test_filter_by_time_range(make_user: Callable[..., User]) -> None:
    """按 start/end 时间范围筛选."""
    admin = make_user(role=Role.ADMIN)
    # 显式构造不同 created_at 的记录（使用 timezone-aware datetime）
    log_old = _seed_log()
    AuditLog.objects.filter(pk=log_old.pk).update(
        created_at=timezone.make_aware(
            timezone.datetime(2026, 1, 1, 0, 0, 0),
            timezone.get_current_timezone(),
        )
    )
    log_new = _seed_log()
    AuditLog.objects.filter(pk=log_new.pk).update(
        created_at=timezone.make_aware(
            timezone.datetime(2026, 7, 31, 12, 0, 0),
            timezone.get_current_timezone(),
        )
    )
    client = Client()
    # 只查 6 月以后
    response = _get(
        client,
        "/api/v1/audit/logs?start=2026-06-01T00:00:00",
        _auth(admin),
    )
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["id"] == log_new.pk


@pytest.mark.django_db
def test_filter_by_time_end(make_user: Callable[..., User]) -> None:
    """按 end 截止时间筛选（覆盖 created_at__lte 分支）."""
    admin = make_user(role=Role.ADMIN)
    log_old = _seed_log()
    AuditLog.objects.filter(pk=log_old.pk).update(
        created_at=timezone.make_aware(
            timezone.datetime(2026, 1, 1, 0, 0, 0),
            timezone.get_current_timezone(),
        )
    )
    log_new = _seed_log()
    AuditLog.objects.filter(pk=log_new.pk).update(
        created_at=timezone.make_aware(
            timezone.datetime(2026, 7, 31, 12, 0, 0),
            timezone.get_current_timezone(),
        )
    )
    client = Client()
    # 只查 6 月以前
    response = _get(
        client,
        "/api/v1/audit/logs?end=2026-06-01T00:00:00",
        _auth(admin),
    )
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["id"] == log_old.pk


@pytest.mark.django_db
def test_filter_by_user_id(make_user: Callable[..., User]) -> None:
    """按 user_id 精确筛选."""
    admin = make_user(role=Role.ADMIN)
    other = make_user(username="other", role=Role.VIEWER)
    _seed_log(user=other, username="other")
    _seed_log(user=admin, username="admin")
    client = Client()
    response = _get(client, f"/api/v1/audit/logs?user_id={other.pk}", _auth(admin))
    body = json.loads(response.content)
    assert body["total"] == 1
    assert body["items"][0]["username"] == "other"


@pytest.mark.django_db
def test_filter_combined(make_user: Callable[..., User]) -> None:
    """多条件组合筛选."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(
        username="alice",
        action=AuditAction.DML_INSERT,
        status=AuditStatus.SUCCESS,
        datasource_id=1,
    )
    _seed_log(
        username="alice",
        action=AuditAction.DML_DELETE,
        status=AuditStatus.FAILURE,
        datasource_id=1,
    )
    _seed_log(
        username="bob",
        action=AuditAction.DML_INSERT,
        status=AuditStatus.SUCCESS,
        datasource_id=2,
    )
    client = Client()
    response = _get(
        client,
        "/api/v1/audit/logs?username=alice&action=dml.insert&status=success&datasource_id=1",
        _auth(admin),
    )
    body = json.loads(response.content)
    assert body["total"] == 1


@pytest.mark.django_db
def test_invalid_datetime_ignored(make_user: Callable[..., User]) -> None:
    """无法解析的时间字符串应被忽略（不报错，返回全部）."""
    admin = make_user(role=Role.ADMIN)
    _seed_log()
    client = Client()
    response = _get(client, "/api/v1/audit/logs?start=not-a-date", _auth(admin))
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["total"] == 1


# ---------- CSV 导出 ----------


def _read_streaming_csv(response: HttpResponse) -> str:
    """从 StreamingHttpResponse 拼接 streaming_content 并解码为字符串（去 BOM）."""
    chunks = b"".join(response.streaming_content)  # type: ignore[union-attr]
    return chunks.decode("utf-8-sig")


@pytest.mark.django_db
def test_export_returns_csv_with_bom(make_user: Callable[..., User]) -> None:
    """CSV 导出应返回 text/csv 且首 3 字节为 UTF-8 BOM."""
    admin = make_user(role=Role.ADMIN)
    _seed_log()
    client = Client()
    response = _get(client, "/api/v1/audit/logs/export", _auth(admin))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    raw = b"".join(response.streaming_content)  # type: ignore[union-attr]
    assert raw[:3] == b"\xef\xbb\xbf"
    assert "attachment" in response["Content-Disposition"]
    assert "audit_logs_" in response["Content-Disposition"]


@pytest.mark.django_db
def test_export_csv_contains_all_rows(make_user: Callable[..., User]) -> None:
    """CSV 导出应包含全部匹配行（不分页）."""
    admin = make_user(role=Role.ADMIN)
    for i in range(5):
        _seed_log(username=f"user{i}")
    client = Client()
    response = _get(client, "/api/v1/audit/logs/export", _auth(admin))
    assert response.status_code == 200
    content = _read_streaming_csv(response)
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 1 表头 + 5 数据行
    assert len(rows) == 6
    # 表头按 _CSV_COLUMNS 顺序
    assert rows[0][0] == "id"
    assert rows[0][1] == "created_at"
    assert rows[0][3] == "action"


@pytest.mark.django_db
def test_export_with_filter(make_user: Callable[..., User]) -> None:
    """导出时筛选条件应生效."""
    admin = make_user(role=Role.ADMIN)
    _seed_log(username="alice")
    _seed_log(username="bob")
    client = Client()
    response = _get(client, "/api/v1/audit/logs/export?username=alice", _auth(admin))
    content = _read_streaming_csv(response)
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 1 表头 + 1 数据行
    assert len(rows) == 2
    # 第 3 列为 username
    assert rows[1][2] == "alice"


@pytest.mark.django_db
def test_export_empty_result(make_user: Callable[..., User]) -> None:
    """无匹配数据时 CSV 仅有表头."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    response = _get(
        client,
        "/api/v1/audit/logs?username=nonexistent",
        _auth(admin),
    )
    body = json.loads(response.content)
    assert body["total"] == 0
    response_exp = _get(
        client,
        "/api/v1/audit/logs/export?username=nonexistent",
        _auth(admin),
    )
    content = _read_streaming_csv(response_exp)
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 1  # 仅表头
