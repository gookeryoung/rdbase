"""Webhook 接收端点测试（iter-54 P8-Q4）.

覆盖 ``POST /api/v1/ingest/webhook/{token}`` 端点：

- token 鉴权：无效 token 404、非 WEBHOOK 源 404、任务未激活 409
- payload 解析：dict / list / 非法格式 / 空数组
- pipeline 执行：写 IngestLog、更新 task.last_sync_at、返回 WebhookReceiveOut
- 幂等回放：相同 Idempotency-Key 二次请求返回缓存结果
- 令牌桶限流：超额返回 429 + Retry-After
- 审计：成功/失败均写 WEBHOOK_RECEIVE 审计日志
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from apps.audit.models import AuditAction, AuditLog
from apps.datasources.engine import dispose_all, get_engine
from apps.datasources.models import DataSource, EngineType
from apps.ingest.models import IngestFieldMapping, IngestLog, IngestLogStatus, IngestTask, SourceType
from apps.ingest.webhook import _build_spider_proxy, _SimpleStats, run_webhook_pipelines
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Any:
    """每个测试前后清空 SQLAlchemy 引擎缓存."""
    dispose_all()
    yield
    dispose_all()


@pytest.fixture
def datasource(db: Any, admin_user: Any) -> DataSource:
    """SQLite 数据源 fixture."""
    return DataSource.objects.create(
        name="ds_webhook",
        engine=EngineType.SQLITE,
        database=":memory:",
        created_by=admin_user,
    )


@pytest.fixture
def webhook_task(db: Any, datasource: DataSource) -> IngestTask:
    """WEBHOOK 源类型任务 fixture（save 时自动生成 webhook_token）."""
    return IngestTask.objects.create(
        name="wh_task",
        source_type=SourceType.WEBHOOK,
        source_url="https://example.com/webhook",
        target_datasource=datasource,
        target_table="out",
    )


def _post_webhook(
    client: Client,
    token: str,
    body: Any,
    *,
    idem_key: str | None = None,
) -> HttpResponse:
    """POST webhook payload."""
    headers: dict[str, str] = {}
    if idem_key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = idem_key
    return cast(
        HttpResponse,
        client.post(
            f"/api/v1/ingest/webhook/{token}",
            data=json.dumps(body) if not isinstance(body, str) else body,
            content_type="application/json",
            **headers,
        ),
    )


class TestWebhookTokenAuth:
    """token 鉴权测试."""

    def test_invalid_token_returns_404(self, client: Client, db: Any) -> None:
        """不存在的 token 应返回 404."""
        resp = _post_webhook(client, "nonexistent_token", {"id": 1})
        assert resp.status_code == 404

    def test_non_webhook_source_returns_404(
        self,
        client: Client,
        db: Any,
        datasource: DataSource,
    ) -> None:
        """非 WEBHOOK 源类型任务的 webhook_token 应返回 404."""
        task = IngestTask.objects.create(
            name="api_task",
            source_type=SourceType.API,
            source_url="https://x.io",
            target_datasource=datasource,
            target_table="t",
        )
        # API 源任务的 webhook_token 应为 None
        assert task.webhook_token is None
        resp = _post_webhook(client, "any_token", {"id": 1})
        assert resp.status_code == 404

    def test_inactive_task_returns_409(
        self,
        client: Client,
        webhook_task: IngestTask,
    ) -> None:
        """任务未激活（paused/error）应返回 409."""
        webhook_task.status = "paused"
        webhook_task.save(update_fields=["status"])
        resp = _post_webhook(client, webhook_task.webhook_token, {"id": 1})
        assert resp.status_code == 409


class TestPayloadParsing:
    """payload 解析测试."""

    def test_dict_payload_wrapped_as_list(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dict payload 应包装为单元素列表."""
        calls: list[list[dict[str, Any]]] = []

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            calls.append(items)
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=len(items),
                rows_written=len(items),
                started_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
                duration_ms=1,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        resp = _post_webhook(client, webhook_task.webhook_token, {"id": 1, "name": "a"})
        assert resp.status_code == 200
        assert calls[0] == [{"id": 1, "name": "a"}]

    def test_list_payload_accepted(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """list payload 应直接使用."""

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=len(items),
                rows_written=len(items),
                started_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
                duration_ms=1,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        resp = _post_webhook(
            client,
            webhook_task.webhook_token,
            [{"id": 1}, {"id": 2}],
        )
        assert resp.status_code == 200
        assert resp.json()["rows_read"] == 2

    def test_invalid_json_returns_400(
        self,
        client: Client,
        webhook_task: IngestTask,
    ) -> None:
        """非法 JSON 应返回 400."""
        resp = _post_webhook(client, webhook_task.webhook_token, "not json")
        assert resp.status_code == 400

    def test_empty_list_returns_400(
        self,
        client: Client,
        webhook_task: IngestTask,
    ) -> None:
        """空数组应返回 400."""
        resp = _post_webhook(client, webhook_task.webhook_token, [])
        assert resp.status_code == 400

    def test_list_with_non_dict_items_returns_400(
        self,
        client: Client,
        webhook_task: IngestTask,
    ) -> None:
        """数组中含非 dict 元素应返回 400."""
        resp = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}, "str", 42])
        assert resp.status_code == 400

    def test_non_object_non_array_returns_400(
        self,
        client: Client,
        webhook_task: IngestTask,
    ) -> None:
        """payload 为非 dict/list 类型应返回 400."""
        resp = _post_webhook(client, webhook_task.webhook_token, 12345)
        assert resp.status_code == 400


class TestPipelineExecution:
    """pipeline 执行与 IngestLog 写入测试."""

    def test_creates_ingest_log(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功执行应创建 IngestLog 并返回 log_id."""
        from django.utils import timezone

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=2,
                rows_written=2,
                rows_skipped=0,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                duration_ms=10,
                quality_score=95.0,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        resp = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}, {"id": 2}])
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == webhook_task.pk
        assert body["rows_read"] == 2
        assert body["rows_written"] == 2
        assert body["quality_score"] == 95.0
        assert IngestLog.objects.filter(task=webhook_task).count() == 1

    def test_updates_task_last_sync_at(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """执行后应更新 task.last_sync_at."""
        from django.utils import timezone

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            # 模拟真实 run_webhook_pipelines 更新 task.last_sync_at 的行为
            now = timezone.now()
            log = IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=1,
                rows_written=1,
                started_at=now,
                finished_at=now,
                duration_ms=5,
            )
            task.last_run_at = now
            task.last_sync_at = now
            task.save(update_fields=["last_run_at", "last_sync_at"])
            return log

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        assert webhook_task.last_sync_at is None
        _post_webhook(client, webhook_task.webhook_token, [{"id": 1}])
        webhook_task.refresh_from_db()
        assert webhook_task.last_sync_at is not None

    def test_pipeline_failure_returns_500(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pipeline 抛异常时应返回 500 并写失败审计."""

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            raise RuntimeError("pipeline 内部错误")

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        resp = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}])
        assert resp.status_code == 500
        assert "pipeline 内部错误" in resp.json()["detail"]


class TestIdempotency:
    """幂等回放测试."""

    def test_same_idempotency_key_returns_cached(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """相同 Idempotency-Key 二次请求应返回缓存结果，pipeline 仅执行一次."""
        from django.utils import timezone

        call_count = {"n": 0}

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            call_count["n"] += 1
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=len(items),
                rows_written=len(items),
                started_at=timezone.now(),
                duration_ms=5,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        resp1 = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}], idem_key="wh-idem-1")
        assert resp1.status_code == 200
        assert call_count["n"] == 1

        resp2 = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}], idem_key="wh-idem-1")
        assert resp2.status_code == 200
        assert call_count["n"] == 1
        assert json.loads(resp1.content) == json.loads(resp2.content)

    def test_different_idempotency_key_runs_independently(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """不同 Idempotency-Key 应独立执行."""
        from django.utils import timezone

        call_count = {"n": 0}

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            call_count["n"] += 1
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=len(items),
                rows_written=len(items),
                started_at=timezone.now(),
                duration_ms=5,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        _post_webhook(client, webhook_task.webhook_token, [{"id": 1}], idem_key="key-a")
        _post_webhook(client, webhook_task.webhook_token, [{"id": 2}], idem_key="key-b")
        assert call_count["n"] == 2


class TestRateLimit:
    """令牌桶限流测试."""

    def test_rate_limit_returns_429(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超额请求应返回 429 + Retry-After."""
        from django.test import override_settings
        from django.utils import timezone

        # 使用极小容量确保快速触发限流
        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=len(items),
                rows_written=len(items),
                started_at=timezone.now(),
                duration_ms=5,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)

        with override_settings(
            RATE_LIMIT_WEBHOOK_CAPACITY=1,
            RATE_LIMIT_WEBHOOK_REFILL_RATE=0.01,
        ):
            from apps.system import rate_limiter

            rate_limiter.reset_rate_limiter()
            try:
                # 第一次请求应放行（容量=1）
                resp1 = _post_webhook(client, webhook_task.webhook_token, [{"id": 1}])
                assert resp1.status_code == 200
                # 第二次请求应被限流
                resp2 = _post_webhook(client, webhook_task.webhook_token, [{"id": 2}])
                assert resp2.status_code == 429
                assert "Retry-After" in resp2.headers
                assert int(resp2.headers["Retry-After"]) >= 1
            finally:
                rate_limiter.reset_rate_limiter()


class TestAuditLog:
    """审计日志写入测试."""

    def test_success_writes_audit(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功执行应写 WEBHOOK_RECEIVE 审计."""
        from django.utils import timezone

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            return IngestLog.objects.create(
                task=task,
                status=IngestLogStatus.SUCCESS,
                rows_read=2,
                rows_written=2,
                started_at=timezone.now(),
                duration_ms=5,
            )

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        _post_webhook(client, webhook_task.webhook_token, [{"id": 1}, {"id": 2}])

        logs = AuditLog.objects.filter(action=AuditAction.WEBHOOK_RECEIVE)
        assert logs.count() == 1
        log = logs.first()
        assert str(webhook_task.pk) == log.resource_id
        assert log.status == "success"

    def test_failure_writes_audit(
        self,
        client: Client,
        webhook_task: IngestTask,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pipeline 失败应写 FAILURE 审计."""

        def fake_run(task: IngestTask, items: list[dict[str, Any]]) -> IngestLog:
            raise RuntimeError("boom")

        monkeypatch.setattr("apps.ingest.api.run_webhook_pipelines", fake_run)
        _post_webhook(client, webhook_task.webhook_token, [{"id": 1}])

        logs = AuditLog.objects.filter(action=AuditAction.WEBHOOK_RECEIVE)
        assert logs.count() == 1
        log = logs.first()
        assert log.status == "failure"
        assert "boom" in log.error_message


class TestSimpleStats:
    """``_SimpleStats`` stats 收集器测试."""

    def test_get_value_default(self) -> None:
        stats = _SimpleStats()
        assert stats.get_value("missing") is None
        assert stats.get_value("missing", 0) == 0

    def test_set_and_get(self) -> None:
        stats = _SimpleStats()
        stats.set_value("key", 42)
        assert stats.get_value("key") == 42

    def test_inc_value(self) -> None:
        stats = _SimpleStats()
        stats.inc_value("count")
        stats.inc_value("count")
        stats.inc_value("count", 10)
        assert stats.get_value("count") == 12


class TestBuildSpiderProxy:
    """``_build_spider_proxy`` 构造测试."""

    def test_proxy_has_required_attributes(self, webhook_task: IngestTask) -> None:
        """代理对象应包含 pipeline 所需的全部属性."""
        proxy = _build_spider_proxy(webhook_task, [])
        assert proxy.task_id == webhook_task.pk
        assert proxy.target_datasource_id == webhook_task.target_datasource_id
        assert proxy.target_table == webhook_task.target_table
        assert proxy.conflict_strategy == webhook_task.conflict_strategy
        assert proxy.batch_size == webhook_task.batch_size
        assert proxy.mappings == []


class TestRunWebhookPipelinesReal:
    """``run_webhook_pipelines`` 真实 pipeline 执行测试.

    不 mock 任何 pipeline，验证完整链路：
    CleaningPipeline → ValidationPipeline → FieldMappingPipeline → 目标表写入。
    """

    def _create_target_table(self, datasource: DataSource) -> None:
        """在目标数据源创建 out 表."""
        engine = get_engine(datasource)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE out (id INTEGER PRIMARY KEY, name TEXT)"))

    def _add_mappings(self, task: IngestTask) -> None:
        """为任务添加 id/name 字段映射."""
        IngestFieldMapping.objects.create(task=task, source_field="id", target_field="id", is_pk=True)
        IngestFieldMapping.objects.create(task=task, source_field="name", target_field="name")

    def test_success_writes_rows_and_log(self, db: Any, webhook_task: IngestTask, datasource: DataSource) -> None:
        """成功执行应写入目标表并创建 SUCCESS IngestLog."""
        self._create_target_table(datasource)
        self._add_mappings(webhook_task)

        items = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        log = run_webhook_pipelines(webhook_task, items)

        assert log.status == IngestLogStatus.SUCCESS
        assert log.rows_read == 2
        assert log.rows_written == 2
        assert log.rows_skipped == 0
        assert log.quality_score == 100.0
        assert log.finished_at is not None
        assert log.duration_ms >= 0

        # 验证目标表写入
        engine = get_engine(datasource)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, name FROM out ORDER BY id"))
            rows = result.fetchall()
        assert rows == [(1, "alice"), (2, "bob")]

    def test_updates_task_last_sync_at(self, db: Any, webhook_task: IngestTask, datasource: DataSource) -> None:
        """执行后应更新 task.last_sync_at 与 last_run_at."""
        self._create_target_table(datasource)
        self._add_mappings(webhook_task)

        assert webhook_task.last_sync_at is None
        run_webhook_pipelines(webhook_task, [{"id": 1, "name": "a"}])
        webhook_task.refresh_from_db()
        assert webhook_task.last_sync_at is not None
        assert webhook_task.last_run_at is not None

    def test_cleaning_drop_marks_partial(self, db: Any, webhook_task: IngestTask, datasource: DataSource) -> None:
        """清洗丢弃部分 item 时应标记为 PARTIAL."""
        self._create_target_table(datasource)
        self._add_mappings(webhook_task)
        # 配置清洗规则：name 缺失则丢弃
        webhook_task.clean_config = {"rules": [{"op": "on_missing", "field": "name", "strategy": "skip"}]}
        webhook_task.save(update_fields=["clean_config"])

        items = [{"id": 1, "name": "ok"}, {"id": 2, "name": ""}]
        log = run_webhook_pipelines(webhook_task, items)

        assert log.status == IngestLogStatus.PARTIAL
        assert log.rows_read == 2
        assert log.rows_written == 1
        assert log.rows_skipped == 1

    def test_empty_items_still_creates_log(self, db: Any, webhook_task: IngestTask, datasource: DataSource) -> None:
        """空 items 列表仍应创建 IngestLog（rows_read=0）."""
        self._create_target_table(datasource)
        self._add_mappings(webhook_task)

        log = run_webhook_pipelines(webhook_task, [])

        assert log.status == IngestLogStatus.SUCCESS
        assert log.rows_read == 0
        assert log.rows_written == 0
        assert log.rows_skipped == 0

    def test_no_mappings_skips_write(self, db: Any, webhook_task: IngestTask, datasource: DataSource) -> None:
        """无字段映射时 pipeline 不写入，但仍创建 SUCCESS 日志."""
        self._create_target_table(datasource)

        log = run_webhook_pipelines(webhook_task, [{"id": 1, "name": "a"}])

        assert log.status == IngestLogStatus.SUCCESS
        assert log.rows_written == 0
        # 目标表应为空
        engine = get_engine(datasource)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM out"))
            assert result.fetchone()[0] == 0
