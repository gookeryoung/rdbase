"""ingest API 接口测试."""

from __future__ import annotations

import json
from datetime import timedelta
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


class TestEndToEnd:
    """端到端流程测试：创建 → 执行 → 查看结果 → 查看日志 → 统计 → 告警.

    不真跑 Scrapy（reactor 不可在 pytest 内反复启停，spider parse 逻辑由
    test_ingest_spiders_* 系列覆盖），通过 monkeypatch spawn_ingest 模拟
    子进程返回，在 mock 内创建真实 IngestLog 与更新任务状态，覆盖 HTTP API
    层 + 数据库状态 + 业务逻辑的全链路。
    """

    def test_create_each_source_type_persists_parse_config(
        self, db: Any, client: Client, admin_user: Any, datasource: DataSource
    ) -> None:
        """四类源类型创建任务，验证 parse_config 与 field_mappings 正确持久化与回显."""
        h = _auth(admin_user)
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "api",
                {"items_path": "data.items", "next_page_path": "data.next", "next_page_max": 5},
            ),
            (
                "html",
                {
                    "selector_type": "xpath",
                    "container_selector": "//div[@class='item']",
                    "fields": {"title": ".//a/text()", "link": {"selector": ".//a", "attr": "href"}},
                    "next_page_selector": "//a[@class='next']",
                    "next_page_attr": "href",
                    "next_page_max": 3,
                },
            ),
            (
                "file",
                {"format": "xlsx", "encoding": "utf-8", "delimiter": ",", "sheet": "Sheet1", "items_path": ""},
            ),
            ("rss", {"include_feed_metadata": True}),
        ]
        for idx, (source_type, parse_config) in enumerate(cases):
            name = f"task_{source_type}_{idx}"
            resp = _post(
                client,
                "/api/v1/ingest/tasks",
                _payload(
                    datasource.pk,
                    name=name,
                    source_type=source_type,
                    source_url=f"https://example.com/{source_type}",
                    parse_config=parse_config,
                ),
                h,
            )
            assert resp.status_code == 201, f"{source_type} 创建失败: {resp.json()}"
            body = resp.json()
            assert body["source_type"] == source_type
            # JSONField 存什么取什么，整体比较
            assert body["parse_config"] == parse_config, f"{source_type} parse_config 不匹配"
            # field_mappings 默认 2 条
            assert len(body["field_mappings"]) == 2
            assert body["field_mappings"][0]["is_pk"] is True

    def test_full_flow_success(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """完整成功链路：创建 → 执行（mock spawn 产生 SUCCESS log）→ 查看结果 → 日志 → 统计."""
        h = _auth(admin_user)
        # 1. 创建任务
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(
                datasource.pk,
                name="e2e_success",
                parse_config={"items_path": "data"},
            ),
            h,
        )
        assert create.status_code == 201
        tid = create.json()["id"]

        # 2. 触发执行（mock spawn 模拟成功）
        def fake_spawn(_task_id: int) -> Any:
            task = IngestTask.objects.get(pk=tid)
            started = timezone.now()
            IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=10,
                rows_written=10,
                rows_skipped=0,
                started_at=started,
                finished_at=timezone.now(),
                duration_ms=42,
            )
            task.last_run_at = timezone.now()
            task.last_sync_at = task.last_run_at
            task.retry_count = 0
            task.save(update_fields=["last_run_at", "last_sync_at", "retry_count"])

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", fake_spawn)

        run = client.post(f"/api/v1/ingest/tasks/{tid}/run", **h)
        assert run.status_code == 200
        run_body = run.json()
        assert run_body["returncode"] == 0
        assert run_body["stderr"] == ""
        assert run_body["log"]["status"] == "success"
        assert run_body["log"]["rows_read"] == 10
        assert run_body["log"]["rows_written"] == 10

        # 3. 查看任务详情，验证 last_sync_at 已更新
        got = client.get(f"/api/v1/ingest/tasks/{tid}", **h)
        assert got.status_code == 200
        assert got.json()["last_sync_at"] is not None
        assert got.json()["retry_count"] == 0

        # 4. 查看任务日志列表
        logs = client.get(f"/api/v1/ingest/tasks/{tid}/logs", **h)
        assert logs.status_code == 200
        logs_body = logs.json()
        assert len(logs_body) == 1
        assert logs_body[0]["status"] == "success"
        assert logs_body[0]["duration_ms"] == 42

        # 5. 查看全局统计
        stats = client.get("/api/v1/ingest/stats", **h)
        assert stats.status_code == 200
        stats_body = stats.json()
        assert stats_body["total"] == 1
        assert stats_body["succeeded"] == 1
        assert stats_body["failed"] == 0
        assert stats_body["success_rate"] == 100.0
        assert stats_body["total_rows_read"] == 10
        assert stats_body["total_rows_written"] == 10

    def test_full_flow_partial(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """部分成功链路：rows_skipped > 0 时 log.status=partial，统计归类正确."""
        h = _auth(admin_user)
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, name="e2e_partial"),
            h,
        )
        tid = create.json()["id"]

        def fake_spawn(_task_id: int) -> Any:
            task = IngestTask.objects.get(pk=tid)
            IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.PARTIAL,
                rows_read=20,
                rows_written=15,
                rows_skipped=5,
                started_at=timezone.now(),
                duration_ms=100,
            )

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", fake_spawn)

        run = client.post(f"/api/v1/ingest/tasks/{tid}/run", **h)
        assert run.status_code == 200
        assert run.json()["log"]["status"] == "partial"
        assert run.json()["log"]["rows_skipped"] == 5

        stats = client.get("/api/v1/ingest/stats", **h)
        stats_body = stats.json()
        assert stats_body["partial"] == 1
        assert stats_body["succeeded"] == 0
        assert stats_body["total_rows_skipped"] == 5

    def test_full_flow_failure_triggers_alert_and_ack(
        self,
        db: Any,
        client: Client,
        admin_user: Any,
        datasource: DataSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """失败链路：执行失败产生告警 → 告警列表显示 → 确认告警 → 列表清空.

        模拟达最大重试的失败场景：直接在 fake_spawn 内创建 FAILED log 与告警，
        验证 HTTP API 层与 IngestAlert 模型联动。
        """
        h = _auth(admin_user)
        # 创建任务并预设 retry_count 已达 max_retries，模拟最后一次失败触发告警
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, name="e2e_fail"),
            h,
        )
        tid = create.json()["id"]
        task = IngestTask.objects.get(pk=tid)
        task.retry_count = task.max_retries - 1  # 下次失败即达上限
        task.save(update_fields=["retry_count"])

        def fake_spawn(_task_id: int) -> Any:
            t = IngestTask.objects.get(pk=tid)
            IngestLog.objects.create(
                task=t,
                status=IngestLogStatus.FAILED,
                rows_read=0,
                rows_written=0,
                rows_skipped=0,
                error_message="连接超时",
                started_at=timezone.now(),
                duration_ms=5000,
            )
            t.retry_count = t.max_retries
            t.last_run_at = timezone.now()
            t.save(update_fields=["retry_count", "last_run_at"])
            IngestAlert.raise_alert(t, f"爬取失败（已达最大重试 {t.max_retries} 次）: 连接超时")

            class _R:
                returncode = 1
                stdout = ""
                stderr = "Traceback (most recent call last):\nConnectionError: timeout"

            return _R()

        monkeypatch.setattr("apps.ingest.api.spawn_ingest", fake_spawn)

        # 1. 触发执行
        run = client.post(f"/api/v1/ingest/tasks/{tid}/run", **h)
        assert run.status_code == 200
        run_body = run.json()
        assert run_body["returncode"] == 1
        assert "ConnectionError" in run_body["stderr"]
        assert run_body["log"]["status"] == "failed"
        assert run_body["log"]["error_message"] == "连接超时"

        # 2. 查看任务详情，状态仍为 active（execute_task 不改 task.status，仅 retry_count）
        got = client.get(f"/api/v1/ingest/tasks/{tid}", **h)
        assert got.json()["retry_count"] == got.json()["max_retries"]

        # 3. 查看告警列表（默认仅未确认）
        alerts = client.get("/api/v1/ingest/alerts", **h)
        assert alerts.status_code == 200
        alerts_body = alerts.json()
        assert len(alerts_body) == 1
        assert alerts_body[0]["level"] == "error"
        assert alerts_body[0]["acknowledged"] is False
        assert "连接超时" in alerts_body[0]["message"]
        alert_id = alerts_body[0]["id"]

        # 4. viewer 不能确认告警
        viewer_token = _auth(_make_viewer(db))
        ack_forbidden = client.post(f"/api/v1/ingest/alerts/{alert_id}/ack", **viewer_token)
        assert ack_forbidden.status_code == 403

        # 5. admin 确认告警
        ack = client.post(f"/api/v1/ingest/alerts/{alert_id}/ack", **h)
        assert ack.status_code == 200
        assert ack.json()["acknowledged"] is True
        assert ack.json()["acknowledged_at"] is not None

        # 6. 默认列表不再返回已确认告警
        alerts2 = client.get("/api/v1/ingest/alerts", **h)
        assert alerts2.json() == []

        # 7. all=true 仍可查到
        alerts3 = client.get("/api/v1/ingest/alerts?all=true", **h)
        assert len(alerts3.json()) == 1

        # 8. 统计反映失败
        stats = client.get("/api/v1/ingest/stats", **h)
        stats_body = stats.json()
        assert stats_body["failed"] == 1
        assert stats_body["succeeded"] == 0
        assert stats_body["success_rate"] == 0.0

    def test_headers_preserved_when_edit_without_resubmit(
        self, db: Any, client: Client, admin_user: Any, datasource: DataSource
    ) -> None:
        """编辑任务时不传 headers，原加密请求头保留;has_headers 标志保持 true."""
        h = _auth(admin_user)
        # 创建带 headers 的任务
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(
                datasource.pk,
                name="e2e_headers",
                headers={"Authorization": "Bearer secret-token-xyz", "X-Api-Key": "abc123"},
            ),
            h,
        )
        assert create.status_code == 201
        tid = create.json()["id"]
        assert create.json()["has_headers"] is True
        # 明文不回显
        assert "headers" not in create.json()

        # 模型层验证加密存储可解密
        task = IngestTask.objects.get(pk=tid)
        decrypted = task.get_headers()
        assert decrypted["Authorization"] == "Bearer secret-token-xyz"
        assert decrypted["X-Api-Key"] == "abc123"

        # PUT 不传 headers 字段（仅改 name）
        upd = client.put(
            f"/api/v1/ingest/tasks/{tid}",
            data=json.dumps({"name": "e2e_headers_renamed"}),
            content_type="application/json",
            **h,
        )
        assert upd.status_code == 200
        assert upd.json()["has_headers"] is True

        # 模型层验证原值仍保留
        task.refresh_from_db()
        assert task.get_headers()["Authorization"] == "Bearer secret-token-xyz"

        # GET 验证 has_headers 仍为 true
        got = client.get(f"/api/v1/ingest/tasks/{tid}", **h)
        assert got.json()["has_headers"] is True

    def test_headers_overwritten_on_resubmit(
        self, db: Any, client: Client, admin_user: Any, datasource: DataSource
    ) -> None:
        """编辑任务时传新 headers，整体覆盖原值."""
        h = _auth(admin_user)
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, name="e2e_overwrite", headers={"X-Old": "v1"}),
            h,
        )
        tid = create.json()["id"]

        # PUT 传新 headers（覆盖）
        upd = client.put(
            f"/api/v1/ingest/tasks/{tid}",
            data=json.dumps({"headers": {"X-New": "v2"}}),
            content_type="application/json",
            **h,
        )
        assert upd.status_code == 200
        assert upd.json()["has_headers"] is True

        # 模型层验证旧 key 消失，新 key 存在
        task = IngestTask.objects.get(pk=tid)
        decrypted = task.get_headers()
        assert "X-Old" not in decrypted
        assert decrypted["X-New"] == "v2"

    def test_viewer_can_read_but_not_write(
        self, db: Any, client: Client, admin_user: Any, regular_user: Any, datasource: DataSource
    ) -> None:
        """viewer 可读列表/详情/日志/统计，但不能创建/执行/确认告警."""
        # admin 先创建一个任务供 viewer 读取
        h_admin = _auth(admin_user)
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, name="e2e_viewer_read"),
            h_admin,
        )
        tid = create.json()["id"]

        h_viewer = _auth(regular_user)

        # 读操作允许
        assert client.get("/api/v1/ingest/tasks", **h_viewer).status_code == 200
        assert client.get(f"/api/v1/ingest/tasks/{tid}", **h_viewer).status_code == 200
        assert client.get(f"/api/v1/ingest/tasks/{tid}/logs", **h_viewer).status_code == 200
        assert client.get("/api/v1/ingest/stats", **h_viewer).status_code == 200
        assert client.get("/api/v1/ingest/alerts", **h_viewer).status_code == 200

        # 写操作禁止
        assert _post(client, "/api/v1/ingest/tasks", _payload(datasource.pk), h_viewer).status_code == 403
        assert client.post(f"/api/v1/ingest/tasks/{tid}/run", **h_viewer).status_code == 403
        # viewer 不能删除/更新
        assert (
            client.put(
                f"/api/v1/ingest/tasks/{tid}",
                data=json.dumps({"name": "hack"}),
                content_type="application/json",
                **h_viewer,
            ).status_code
            == 403
        )
        assert client.delete(f"/api/v1/ingest/tasks/{tid}", **h_viewer).status_code == 403

    def test_stats_days_filter(self, db: Any, client: Client, admin_user: Any, datasource: DataSource) -> None:
        """stats ?days=N 仅统计最近 N 天的日志."""
        h = _auth(admin_user)
        create = _post(
            client,
            "/api/v1/ingest/tasks",
            _payload(datasource.pk, name="e2e_days"),
            h,
        )
        tid = create.json()["id"]
        task = IngestTask.objects.get(pk=tid)
        # 创建一条 10 天前的日志
        old_started = timezone.now() - timedelta(days=10)
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.SUCCESS,
            rows_read=1,
            rows_written=1,
            started_at=old_started,
            duration_ms=5,
        )
        # 创建一条今天的日志
        IngestLog.objects.create(
            task=task,
            status=IngestLogStatus.FAILED,
            rows_read=2,
            rows_written=0,
            started_at=timezone.now(),
            duration_ms=8,
        )

        # 不传 days：全部
        all_stats = client.get("/api/v1/ingest/stats", **h).json()
        assert all_stats["total"] == 2

        # days=7：仅今天 1 条
        recent_stats = client.get("/api/v1/ingest/stats?days=7", **h).json()
        assert recent_stats["total"] == 1
        assert recent_stats["failed"] == 1
        assert recent_stats["succeeded"] == 0

        # days=30：含 10 天前
        month_stats = client.get("/api/v1/ingest/stats?days=30", **h).json()
        assert month_stats["total"] == 2


def _make_viewer(db: Any) -> User:
    """创建 viewer 用户（供权限测试使用）."""
    from apps.accounts.models import Role

    return User.objects.create_user(
        username="e2e_viewer",
        password="pass1234",
        role=Role.VIEWER,
    )
