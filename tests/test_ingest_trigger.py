"""爬取任务外部触发 API（POST /tasks/{id}/trigger）端到端测试.

覆盖：
- 成功触发：返回 200 + returncode + log，写 INGEST_TRIGGER 审计。
- 任务不存在返回 404。
- 锁占用返回 409。
- 幂等命中返回缓存结果。
- scope 不足返回 403；无 Token 返回 401。
- spawn_ingest 失败返回 500 并写 FAILURE 审计。
- JWT 访问被拒绝（须 ApiToken）。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from apps.accounts.models import ApiToken, User
from apps.audit.models import AuditAction, AuditLog, AuditStatus
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import (
    IngestLog,
    IngestLogStatus,
    IngestTask,
    SourceType,
)
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone


def _make_token(user: User, scopes: list[str] | None = None) -> tuple[str, ApiToken]:
    """生成 ApiToken（默认 sync:trigger scope）."""
    return ApiToken.generate(
        name="ingest-trigger-token",
        created_by=user,
        scopes=scopes if scopes is not None else ["sync:trigger"],
    )


def _token_client(plaintext: str) -> Client:
    """构造携带 X-API-Token 头的客户端."""
    return Client(HTTP_X_API_TOKEN=plaintext)


def _post(client: Client, url: str) -> HttpResponse:
    return cast(HttpResponse, client.post(url))


def _post_idem(client: Client, url: str, idem_key: str) -> HttpResponse:
    return cast(HttpResponse, client.post(url, HTTP_IDEMPOTENCY_KEY=idem_key))


@pytest.fixture
def ingest_ds(db: Any, admin_user: User) -> DataSource:
    """SQLite 数据源."""
    return DataSource.objects.create(
        name="ds-ingest-trigger",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def ingest_task(db: Any, admin_user: User, ingest_ds: DataSource) -> IngestTask:
    """爬取任务."""
    return IngestTask.objects.create(
        name="trigger-task",
        source_type=SourceType.API,
        source_url="https://example.com/api",
        auth_type="none",
        target_datasource=ingest_ds,
        target_table="out",
        conflict_strategy="upsert",
        batch_size=100,
        obey_robots=True,
        scheduler_enabled=False,
        created_by=admin_user,
    )


class _FakeResult:
    """spawn_ingest 子进程返回结果替身."""

    returncode = 0
    stdout = ""
    stderr = ""


def _fake_spawn_success(task_id: int) -> _FakeResult:
    """模拟 spawn_ingest 成功：写一条 SUCCESS 日志."""
    task = IngestTask.objects.get(pk=task_id)
    IngestLog.objects.create(
        task=task,
        status=IngestLogStatus.SUCCESS,
        rows_read=5,
        rows_written=5,
        rows_skipped=0,
        started_at=timezone.now(),
        finished_at=timezone.now(),
        duration_ms=42,
    )
    return _FakeResult()


class TestIngestTrigger:
    """外部触发端点测试."""

    def test_trigger_success(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功触发应返回 200 + returncode + log."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        monkeypatch.setattr("apps.ingest.api.spawn_ingest", _fake_spawn_success)

        resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == ingest_task.pk
        assert body["returncode"] == 0
        assert body["log"] is not None
        assert body["log"]["rows_written"] == 5

    def test_trigger_writes_audit(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功触发应写 INGEST_TRIGGER 审计."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        monkeypatch.setattr("apps.ingest.api.spawn_ingest", _fake_spawn_success)

        _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")

        audit = AuditLog.objects.filter(
            action=AuditAction.INGEST_TRIGGER,
            resource_type="ingest_task",
            resource_id=str(ingest_task.pk),
            status=AuditStatus.SUCCESS,
        )
        assert audit.exists()

    def test_trigger_task_not_found(
        self,
        client: Client,
        admin_user: User,
    ) -> None:
        """任务不存在应返回 404."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, "/api/v1/ingest/tasks/99999/trigger")
        assert resp.status_code == 404

    def test_trigger_lock_contention_409(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
    ) -> None:
        """锁被占用应返回 409."""
        from apps.system.distributed_lock import DistributedLock

        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        holder = DistributedLock(f"ingest:task:{ingest_task.pk}")
        assert holder.acquire() is True
        try:
            resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
            assert resp.status_code == 409
        finally:
            holder.release()

    def test_trigger_idempotency_cached(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """相同 Idempotency-Key 应返回缓存结果，spawn_ingest 仅执行一次."""
        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)

        call_count = {"n": 0}

        def _spawn(_tid: int) -> _FakeResult:
            call_count["n"] += 1
            return _fake_spawn_success(_tid)

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", _spawn)

        resp1 = _post_idem(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger", "idem-trigger-1")
        resp2 = _post_idem(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger", "idem-trigger-1")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()
        assert call_count["n"] == 1

    def test_trigger_scope_insufficient(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
    ) -> None:
        """无 sync:trigger scope 应返回 403."""
        plaintext, _ = _make_token(admin_user, scopes=["datasets:read"])
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
        assert resp.status_code == 403

    def test_trigger_no_token_401(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
    ) -> None:
        """无 Token 应返回 401."""
        c = Client()
        resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
        assert resp.status_code == 401

    def test_trigger_jwt_rejected(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
    ) -> None:
        """JWT 访问应被拒绝（须 ApiToken）."""
        from apps.accounts.jwt import create_access_token

        token = create_access_token(admin_user.pk, str(admin_user.role))
        c = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
        # JWTAuth 通过但 _require_scope 校验 ApiToken 失败 → 403
        assert resp.status_code in {401, 403}

    def test_trigger_spawn_failure_500(
        self,
        client: Client,
        admin_user: User,
        ingest_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """spawn_ingest 失败应返回 500 并写 FAILURE 审计."""

        def _spawn_fail(_tid: int) -> Any:
            raise OSError("subprocess failed")

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", _spawn_fail)

        plaintext, _ = _make_token(admin_user)
        c = _token_client(plaintext)
        resp = _post(c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
        assert resp.status_code == 500

        audit = AuditLog.objects.filter(
            action=AuditAction.INGEST_TRIGGER,
            resource_type="ingest_task",
            resource_id=str(ingest_task.pk),
            status=AuditStatus.FAILURE,
        )
        assert audit.exists()
