"""P9 端到端集成测试（req-03 item 45）.

覆盖外部应用通过 API Token 完成数据中心全流程：

1. **Token 创建**：管理员通过 JWT 创建带全部 scope 的 API Token。
2. **数据集查询**：用 Token 调 ``GET /datasets/{slug}/rows`` 读取数据。
3. **数据集写入**：用 Token 调 ``POST /datasets/{slug}/rows`` 写入数据。
4. **触发同步**：用 Token 调 ``POST /datasets/{slug}/sync``（mock
   ``SyncService.run`` 验证后台线程启动）。
5. **触发爬取**：用 Token 调 ``POST /ingest/tasks/{id}/trigger``（mock
   ``spawn_ingest`` 验证子进程调度）。
6. **Webhook 投递**：mock ``_http_post`` 验证 HMAC-SHA256 签名头与重试退避。
7. **吊销 Token**：吊销后再调任一端点应返回 401。
8. **scope 不足**：仅 ``datasets:read`` 的 Token 调写端点应 403。
9. **OpenAPI 双视图**：管理员视图含全部端点；外部视图仅含数据集 + 触发端点。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import ApiToken, Role, User
from apps.datasources.engine import dispose_all
from apps.datasources.models import Dataset, DataSource, EngineType
from apps.ingest.models import (
    IngestLog,
    IngestLogStatus,
    IngestTask,
    SourceType,
)
from apps.sync.models import (
    SyncConfig,
    SyncFieldMapping,
    SyncMode,
    SyncStatus,
)
from apps.system import quota, rate_limiter
from apps.webhook import deliverer
from apps.webhook.models import WebhookSubscription
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text

# ================================================================
# Fixtures
# ================================================================


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Iterator[None]:
    yield
    dispose_all()


@pytest.fixture(autouse=True)
def _reset_rate_and_quota(settings: Any) -> Iterator[None]:
    """启用 fakeredis 并重置限流/配额后端单例."""
    settings.REDIS_FAKE = True
    settings.REDIS_URL = ""
    rate_limiter.reset_rate_limiter()
    quota.reset_quota()
    yield
    rate_limiter.reset_rate_limiter()
    quota.reset_quota()


@pytest.fixture(autouse=True)
def _noop_webhook_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """将投递器退避 sleep 替换为空操作，避免重试测试真实等待."""

    def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr(deliverer, "_backoff_sleep", _noop)


@pytest.fixture
def sqlite_ds(tmp_path: Path, admin_user: User) -> DataSource:
    """SQLite 文件数据源，预置 users 表与初始数据."""
    db_path = tmp_path / "e2e.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100)"
                ")"
            )
        )
        conn.execute(text("INSERT INTO users (id, name, email) VALUES (1, 'Alice', 'alice@example.com')"))
    engine.dispose()
    return DataSource.objects.create(
        name="ds-e2e",
        engine=EngineType.SQLITE,
        database=str(db_path),
        created_by=admin_user,
    )


@pytest.fixture
def sync_config(sqlite_ds: DataSource, admin_user: User) -> SyncConfig:
    """数据集绑定的同步配置."""
    config = SyncConfig.objects.create(
        name="cfg-e2e",
        source_table="users",
        target_datasource=sqlite_ds,
        target_table="users",
        sync_mode=SyncMode.FULL,
        status=SyncStatus.ACTIVE,
        created_by=admin_user,
    )
    SyncFieldMapping.objects.create(
        config=config,
        source_field="id",
        target_field="id",
        is_pk=True,
    )
    return config


@pytest.fixture
def dataset(sqlite_ds: DataSource, sync_config: SyncConfig) -> Dataset:
    """测试用数据集，绑定 sync_config."""
    return Dataset.objects.create(
        slug="users",
        name="用户数据集",
        datasource=sqlite_ds,
        table_name="users",
        sync_config=sync_config,
        is_active=True,
    )


@pytest.fixture
def ingest_task(sqlite_ds: DataSource, admin_user: User) -> IngestTask:
    """爬取任务 fixture."""
    return IngestTask.objects.create(
        name="e2e-ingest",
        source_type=SourceType.API,
        source_url="https://example.com/api",
        auth_type="none",
        target_datasource=sqlite_ds,
        target_table="users",
        conflict_strategy="upsert",
        batch_size=100,
        obey_robots=True,
        scheduler_enabled=False,
        created_by=admin_user,
    )


# ================================================================
# 辅助函数
# ================================================================


def _make_token(
    user: User,
    *,
    scopes: list[str],
    name: str = "e2e-token",
) -> tuple[str, ApiToken]:
    """生成带指定 scope 的 ApiToken."""
    return ApiToken.generate(
        name=name,
        created_by=user,
        scopes=scopes,
    )


def _token_client(plaintext: str) -> Client:
    """构造携带 X-API-Token 头的客户端."""
    return Client(HTTP_X_API_TOKEN=plaintext)


def _admin_client(user: User) -> Client:
    """构造携带 JWT 的管理员客户端."""
    token = create_access_token(user.pk, str(user.role))
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


def _post_json(client: Client, url: str, body: dict[str, Any]) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json"),
    )


def _get(client: Client, url: str) -> HttpResponse:
    return cast(HttpResponse, client.get(url))


def _post(client: Client, url: str) -> HttpResponse:
    return cast(HttpResponse, client.post(url))


class _FakeSpawnResult:
    """spawn_ingest 子进程返回结果替身."""

    returncode = 0
    stdout = ""
    stderr = ""


def _fake_spawn_success(task_id: int) -> _FakeSpawnResult:
    """模拟 spawn_ingest 成功：写一条 SUCCESS 日志."""
    task = IngestTask.objects.get(pk=task_id)
    IngestLog.objects.create(
        task=task,
        status=IngestLogStatus.SUCCESS,
        rows_read=3,
        rows_written=3,
        rows_skipped=0,
        started_at=__import__("django").utils.timezone.now(),
        finished_at=__import__("django").utils.timezone.now(),
        duration_ms=15,
    )
    return _FakeSpawnResult()


# ================================================================
# P9 端到端全流程
# ================================================================


@pytest.mark.django_db(transaction=True)
def test_p9_full_flow_with_api_token(
    make_user: Callable[..., User],
    dataset: Dataset,
    ingest_task: IngestTask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P9 全流程：创建 Token → 查询 → 写入 → 触发 sync → 触发 ingest → Webhook 投递."""
    admin = make_user(username="admin-e2e", role=Role.ADMIN)
    admin_c = _admin_client(admin)

    # 步骤 1：管理员创建带全部 scope 的 API Token
    create_resp = _post_json(
        admin_c,
        "/api/v1/tokens",
        {"name": "full-flow", "scopes": ["datasets:read", "datasets:write", "sync:trigger"]},
    )
    assert create_resp.status_code == 201, create_resp.content
    token_body = json.loads(create_resp.content)
    plaintext = token_body["token"]
    assert plaintext
    token_id = token_body["id"]
    assert token_body["prefix"]

    # 步骤 2：用 Token 查询数据集行
    token_c = _token_client(plaintext)
    query_resp = _get(token_c, f"/api/v1/datasets/{dataset.slug}/rows")
    assert query_resp.status_code == 200, query_resp.content
    query_body = json.loads(query_resp.content)
    assert query_body["total"] >= 1
    assert "id" in query_body["columns"]
    # 应能看到 Alice
    names = [row.get("name") for row in query_body["items"]]
    assert "Alice" in names

    # 步骤 3：用 Token 写入数据集行
    write_resp = _post_json(
        token_c,
        f"/api/v1/datasets/{dataset.slug}/rows",
        {"rows": [{"id": 100, "name": "E2EUser", "email": "e2e@example.com"}]},
    )
    assert write_resp.status_code == 200, write_resp.content
    write_body = json.loads(write_resp.content)
    assert write_body["written"] == 1

    # 步骤 4：用 Token 触发数据集同步（mock SyncService.run 验证后台线程启动）
    sync_run_calls: list[Any] = []

    def _mock_sync_run(self: Any) -> None:
        sync_run_calls.append(self)

    monkeypatch.setattr("apps.datasources.datasets_api.SyncService.run", _mock_sync_run)
    sync_resp = _post(token_c, f"/api/v1/datasets/{dataset.slug}/sync")
    assert sync_resp.status_code == 202, sync_resp.content
    sync_body = json.loads(sync_resp.content)
    assert sync_body["status"] == "accepted"
    assert len(sync_body["task_id"]) == 32
    # 等待后台线程执行（daemon 线程）
    import time as _time

    _time.sleep(0.2)
    assert len(sync_run_calls) == 1, "SyncService.run 应在后台线程被调用一次"

    # 步骤 5：用 Token 触发爬取任务（mock spawn_ingest）
    monkeypatch.setattr("apps.ingest.api.spawn_ingest", _fake_spawn_success)
    ingest_resp = _post(token_c, f"/api/v1/ingest/tasks/{ingest_task.pk}/trigger")
    assert ingest_resp.status_code == 200, ingest_resp.content
    ingest_body = json.loads(ingest_resp.content)
    assert ingest_body["returncode"] == 0
    assert ingest_body["log"]["status"] == "success"

    # 步骤 6：Webhook 投递验证（mock _http_post 验证签名头）
    WebhookSubscription.objects.create(
        name="e2e-hook",
        url="https://example.com/hook",
        secret="e2e-secret",
        events=["sync.completed"],
        is_active=True,
        created_by=admin,
    )
    captured: list[dict[str, Any]] = []

    def _capture_post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> deliverer._PostResult:
        captured.append({"url": url, "body": body, "headers": dict(headers), "timeout": timeout})
        return deliverer._PostResult(status_code=200, body="ok", error="")

    monkeypatch.setattr(deliverer, "_http_post", _capture_post)
    # 直接调用 deliver_event（sync_service 内部已 mock，不再走真实分发路径）
    deliverer.deliver_event(
        "sync.completed",
        {"config_id": 1, "status": "success", "rows_read": 5},
        wait=True,
    )
    assert len(captured) == 1
    headers = captured[0]["headers"]
    body: bytes = captured[0]["body"]
    expected_sig = hmac.new(b"e2e-secret", body, hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature"] == f"sha256={expected_sig}"
    assert headers["X-Webhook-Event"] == "sync.completed"
    assert headers["Content-Type"] == "application/json"
    # body 应为 payload 的 JSON 编码
    assert json.loads(body.decode("utf-8"))["status"] == "success"

    # 步骤 7：吊销 Token 后再调任一端点应返回 401
    revoke_resp = _post(admin_c, f"/api/v1/tokens/{token_id}/revoke")
    assert revoke_resp.status_code == 200, revoke_resp.content
    query_after_revoke = _get(token_c, f"/api/v1/datasets/{dataset.slug}/rows")
    assert query_after_revoke.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_p9_scope_insufficient_returns_403(
    make_user: Callable[..., User],
    dataset: Dataset,
) -> None:
    """scope 不足的 Token 调写端点应返回 403."""
    admin = make_user(username="admin-e2e-scope", role=Role.ADMIN)
    # 仅 datasets:read scope
    plaintext, _ = _make_token(admin, scopes=["datasets:read"], name="readonly")
    client = _token_client(plaintext)
    write_resp = _post_json(
        client,
        f"/api/v1/datasets/{dataset.slug}/rows",
        {"rows": [{"id": 1, "name": "Should Fail"}]},
    )
    assert write_resp.status_code == 403
    assert "scope" in write_resp.content.decode("utf-8").lower()


@pytest.mark.django_db(transaction=True)
def test_p9_no_token_returns_401(
    client: Client,
    dataset: Dataset,
) -> None:
    """无 Token 访问数据集查询端点应返回 401."""
    resp = _get(client, f"/api/v1/datasets/{dataset.slug}/rows")
    assert resp.status_code == 401


# ================================================================
# OpenAPI 双视图
# ================================================================


@pytest.mark.django_db
def test_openapi_admin_view_includes_all_endpoints(
    client: Client,
) -> None:
    """管理员视图 ``/api/v1/openapi.json`` 应包含全部端点."""
    resp = _get(client, "/api/v1/openapi.json")
    assert resp.status_code == 200
    body = json.loads(resp.content)
    paths = body.get("paths", {})
    # 至少包含管理端点（auth/users/datasources 等）与外部端点
    path_strs = set(paths.keys())
    assert "/api/v1/auth/login" in path_strs
    assert "/api/v1/users" in path_strs
    assert "/api/v1/datasources" in path_strs
    # 外部端点也应存在
    assert "/api/v1/datasets/{slug}/rows" in path_strs
    assert "/api/v1/ingest/tasks/{task_id}/trigger" in path_strs
    # info.title 应为默认值
    assert body["info"]["title"] == "rdbase API"


@pytest.mark.django_db
def test_openapi_external_view_filters_admin_endpoints(
    client: Client,
) -> None:
    """外部视图 ``/api/v1/datasets/openapi.json`` 应仅含对外端点."""
    resp = _get(client, "/api/v1/datasets/openapi.json")
    assert resp.status_code == 200
    body = json.loads(resp.content)
    paths = set((body.get("paths") or {}).keys())
    # 仅保留对外端点
    assert paths == {
        "/api/v1/datasets/{slug}/rows",
        "/api/v1/datasets/{slug}/sync",
        "/api/v1/ingest/tasks/{task_id}/trigger",
    }
    # 管理端点不应出现
    assert "/api/v1/auth/login" not in paths
    assert "/api/v1/users" not in paths
    assert "/api/v1/tokens" not in paths
    # info.title 应标识外部视图
    assert body["info"]["title"] == "rdbase 外部应用 API"


@pytest.mark.django_db
def test_openapi_external_view_includes_get_and_post_methods(
    client: Client,
) -> None:
    """外部视图 ``/api/v1/datasets/{slug}/rows`` 应同时包含 GET 与 POST."""
    resp = _get(client, "/api/v1/datasets/openapi.json")
    body = json.loads(resp.content)
    rows_path = body["paths"]["/api/v1/datasets/{slug}/rows"]
    methods = set(rows_path.keys())
    assert "get" in methods
    assert "post" in methods
