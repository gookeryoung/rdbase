"""ingest API 接口测试."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import User
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import IngestAlert, IngestLog, IngestLogStatus, IngestTask
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_api",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


def _payload(ds_id: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "api_task",
        "source_type": "api",
        "source_url": "https://example.com/api",
        "target_datasource_id": ds_id,
        "target_table": "out",
        "field_mappings": [
            {"source_field": "id", "target_field": "id", "is_pk": True},
            {"source_field": "name", "target_field": "title"},
        ],
    }
    base.update(overrides)
    return base


def _post(client: Client, url: str, body: dict[str, Any], h: dict[str, str]) -> HttpResponse:
    return cast(HttpResponse, client.post(url, data=json.dumps(body), content_type="application/json", **h))


class TestTaskCRUD:
    """任务 CRUD 与权限测试."""

    def test_list_empty(self, db: Any, client: Client, regular_user: Any) -> None:
        resp = client.get("/api/v1/ingest/tasks", **_auth(regular_user))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_admin_success(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        resp = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, headers={"X-Key": "v"}),
            _auth(admin_user),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "api_task"
        assert body["has_headers"] is True
        assert len(body["field_mappings"]) == 2
        assert body["field_mappings"][0]["is_pk"] is True
        assert "headers" not in body

    def test_create_viewer_forbidden(self, db: Any, client: Client, regular_user: Any, datasource: DataSource) -> None:
        resp = _post(client, "/api/v1/ingest/tasks", _payload(datasource.pk), _auth(regular_user))
        assert resp.status_code == 403

    def test_create_invalid_source_type(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        resp = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, source_type="unknown"),
            _auth(admin_user),
        )
        assert resp.status_code == 400

    def test_create_missing_datasource(self, db: Any, client: Client, admin_user: Any) -> None:
        resp = _post(client, "/api/v1/ingest/tasks", _payload(99999), _auth(admin_user))
        assert resp.status_code == 404

    def test_create_invalid_cron(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        resp = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, scheduler_enabled=True, cron_expression="bad"),
            _auth(admin_user),
        )
        assert resp.status_code == 400

    def test_create_schedulable_sets_next_run(
        self, db: Any, client: Client, admin_user: Any, datasource: DataSource
    ) -> None:
        resp = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, scheduler_enabled=True, cron_expression="*/5 * * * *"),
            _auth(admin_user),
        )
        assert resp.status_code == 201
        assert resp.json()["next_run_at"] is not None

    def test_get_update_delete(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        h = _auth(admin_user)
        create = _post(client, "/api/v1/ingest/tasks", _payload(datasource.pk), h)
        tid = create.json()["id"]

        got = client.get(f"/api/v1/ingest/tasks/{tid}", **h)
        assert got.status_code == 200
        assert got.json()["id"] == tid

        upd = client.put(
            f"/api/v1/ingest/tasks/{tid}",
            data=json.dumps({"name": "renamed", "target_table": "out2"}),
            content_type="application/json",
            **h,
        )
        assert upd.status_code == 200
        assert upd.json()["name"] == "renamed"
        assert upd.json()["target_table"] == "out2"

        dele = client.delete(f"/api/v1/ingest/tasks/{tid}", **h)
        assert dele.status_code == 200
        assert not IngestTask.objects.filter(pk=tid).exists()


class TestRunAndLogs:
    """执行与日志测试."""

    def test_run_task_calls_spawn(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        h = _auth(admin_user)
        create = _post(client, "/api/v1/ingest/tasks", _payload(datasource.pk), h)
        tid = create.json()["id"]

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_spawn(_task_id: int) -> Any:
            task = IngestTask.objects.get(pk=tid)
            IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=3,
                rows_written=3,
                started_at=timezone.now(),
                duration_ms=10,
            )
            return FakeResult()

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", fake_spawn)
        resp = client.post(f"/api/v1/ingest/tasks/{tid}/run", **h)
        assert resp.status_code == 200
        body = resp.json()
        assert body["returncode"] == 0
        assert body["log"]["rows_written"] == 3

    def test_list_logs(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        h = _auth(admin_user)
        create = _post(client, "/api/v1/ingest/tasks", _payload(datasource.pk), h)
        tid = create.json()["id"]
        task = IngestTask.objects.get(pk=tid)
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=1,
            rows_written=1,
            started_at=timezone.now(),
            duration_ms=5,
        )
        resp = client.get(f"/api/v1/ingest/tasks/{tid}/logs", **h)
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) == 1
        assert logs[0]["status"] == "success"


class TestAlertsAndStats:
    """告警与统计测试."""

    def test_alerts_list_and_ack(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        h = _auth(admin_user)
        task = IngestTask.objects.create(
            name="a1",
            source_type="api",
            source_url="https://x.io",
            target_datasource=datasource,
            target_table="t",
        )
        alert = IngestAlert.raise_alert(task, "失败")

        resp = client.get("/api/v1/ingest/alerts", **h)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        ack = client.post(f"/api/v1/ingest/alerts/{alert.pk}/ack", **h)
        assert ack.status_code == 200
        assert ack.json()["acknowledged"] is True

        resp2 = client.get("/api/v1/ingest/alerts", **h)
        assert resp2.json() == []

        resp3 = client.get("/api/v1/ingest/alerts?all=true", **h)
        assert len(resp3.json()) == 1

    def test_stats(self, db: Any, client: Client, admin_user: Any) -> None:
        resp = client.get("/api/v1/ingest/stats", **_auth(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["success_rate"] == 0.0
