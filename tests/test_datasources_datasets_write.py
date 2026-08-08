"""数据集写入 API（POST /{slug}/rows）端到端测试.

覆盖：

- 单行/批量 UPSERT、SKIP 冲突跳过、ERROR 冲突报错。
- 无主键表（非 error 策略 400；error 策略放行）。
- ``fields_whitelist`` 列级写权限校验（非白名单列 400）。
- 不存在的列 400、rows 为空 400、单批超限 400、非法冲突策略 400。
- 数据集/数据源 ``is_active=False`` 404。
- 无 Token 401、无 ``datasets:write`` scope 403。
- 限流 429、每日配额超限 429。
- 幂等：重复 ``Idempotency-Key`` 返回缓存结果。
- 审计日志记录 ``DATASET_WRITE``。
- ``pk_fields`` 显式传入。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from apps.accounts.models import ApiToken, Role, User
from apps.audit.models import AuditAction, AuditLog
from apps.datasources.engine import dispose_all
from apps.datasources.models import Dataset, DataSource, EngineType
from apps.system import quota, rate_limiter
from django.http import HttpResponse
from django.test import Client
from sqlalchemy import create_engine, text


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> Iterator[None]:
    """每个测试后清空引擎缓存."""
    yield
    dispose_all()


@pytest.fixture(autouse=True)
def _reset_rate_and_quota(settings: Any) -> Iterator[None]:
    """启用 fakeredis 并在每个测试前后重置限流/配额后端单例."""
    settings.REDIS_FAKE = True
    settings.REDIS_URL = ""
    rate_limiter.reset_rate_limiter()
    quota.reset_quota()
    yield
    rate_limiter.reset_rate_limiter()
    quota.reset_quota()


# ================================================================
# 辅助函数
# ================================================================


def _make_sqlite_file_ds(tmp_path: Path, name: str = "ds-write") -> DataSource:
    """构造基于临时文件的 SQLite 数据源并预置 users 表与 3 行数据."""
    db_path = tmp_path / "write.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(50) NOT NULL, "
                "email VARCHAR(100), "
                "age INTEGER DEFAULT 0, "
                "is_active INTEGER DEFAULT 1"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, name, email, age, is_active) VALUES "
                "(1, 'Alice', 'alice@example.com', 30, 1), "
                "(2, 'Bob', 'bob@example.com', 25, 1)"
            )
        )
        # 无主键表（用于 error 策略与冲突校验测试）
        conn.execute(text("CREATE TABLE events (name VARCHAR(50), value INTEGER)"))
    engine.dispose()
    return DataSource.objects.create(
        name=name,
        engine=EngineType.SQLITE,
        database=str(db_path),
    )


def _make_dataset(
    ds: DataSource,
    *,
    slug: str = "write-target",
    table_name: str = "users",
    fields_whitelist: list[str] | None = None,
    is_active: bool = True,
) -> Dataset:
    """创建 Dataset 实例."""
    return Dataset.objects.create(
        slug=slug,
        name="写入目标",
        datasource=ds,
        table_name=table_name,
        fields_whitelist=fields_whitelist or [],
        is_active=is_active,
    )


def _make_token(
    user: User,
    *,
    scopes: list[str] | None = None,
) -> tuple[str, ApiToken]:
    """生成 ApiToken（默认 datasets:write scope）."""
    return ApiToken.generate(
        name="write-token",
        created_by=user,
        scopes=scopes if scopes is not None else ["datasets:write"],
    )


def _token_client(plaintext: str) -> Client:
    """构造携带 X-API-Token 头的客户端."""
    return Client(HTTP_X_API_TOKEN=plaintext)


def _post(client: Client, url: str, body: dict[str, object]) -> HttpResponse:
    """发送 POST 请求（JSON 体）."""
    return cast(
        HttpResponse,
        client.post(url, data=json.dumps(body), content_type="application/json"),
    )


def _post_idem(
    client: Client,
    url: str,
    body: dict[str, object],
    idem_key: str,
) -> HttpResponse:
    """发送带 Idempotency-Key 头的 POST 请求."""
    return cast(
        HttpResponse,
        client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=idem_key,
        ),
    )


# ================================================================
# 写入主流程
# ================================================================


@pytest.mark.django_db
def test_write_single_row_upsert(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """单行 UPSERT 新行应写入成功."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": [{"id": 10, "name": "Dave", "email": "dave@x.com", "age": 40}]},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"written": 1, "skipped": 0, "total": 1}


@pytest.mark.django_db
def test_write_batch_upsert(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """批量 UPSERT 多行应全部写入."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {
            "rows": [
                {"id": 10, "name": "Dave"},
                {"id": 11, "name": "Eve"},
            ],
        },
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 2
    assert body["skipped"] == 0
    assert body["total"] == 2


@pytest.mark.django_db
def test_write_upsert_updates_existing(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """UPSERT 已存在主键应更新而非新增."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    # id=1 已存在（Alice），UPSERT 更新 name
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": [{"id": 1, "name": "AliceUpdated"}]},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 1


@pytest.mark.django_db
def test_write_skip_strategy(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """SKIP 策略：已存在主键应跳过（skipped 计数）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {
            "rows": [{"id": 1, "name": "Skipped"}],
            "conflict_strategy": "skip",
        },
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["skipped"] == 1
    assert body["written"] == 0


@pytest.mark.django_db
def test_write_error_strategy_conflict(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """ERROR 策略：已存在主键应报错（write_rows 抛 ValueError → 400）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {
            "rows": [{"id": 1, "name": "Conflict"}],
            "conflict_strategy": "error",
        },
    )
    # ERROR 策略下冲突触发 INSERT 异常，整批失败 → 400
    assert response.status_code == 400
    # 失败也应记录审计日志
    log = AuditLog.objects.filter(
        action=AuditAction.DATASET_WRITE,
        status="failure",
    ).first()
    assert log is not None


@pytest.mark.django_db
def test_write_error_strategy_new_row(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """ERROR 策略：新行应正常写入."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {
            "rows": [{"id": 99, "name": "New"}],
            "conflict_strategy": "error",
        },
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 1


# ================================================================
# 无主键表
# ================================================================


@pytest.mark.django_db
def test_write_no_pk_non_error_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """无主键表且策略非 error 时应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="events", table_name="events")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/events/rows",
        {"rows": [{"name": "e1", "value": 1}], "conflict_strategy": "upsert"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_write_no_pk_error_strategy_ok(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """无主键表 + error 策略应放行（纯 INSERT）."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="events", table_name="events")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/events/rows",
        {"rows": [{"name": "e1", "value": 1}], "conflict_strategy": "error"},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 1


# ================================================================
# 列级写权限与字段校验
# ================================================================


@pytest.mark.django_db
def test_write_fields_whitelist_subset_ok(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """白名单非空时，rows 键是白名单子集应放行."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="wl", fields_whitelist=["id", "name"])
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/wl/rows",
        {"rows": [{"id": 50, "name": "Whitelist"}]},
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 1


@pytest.mark.django_db
def test_write_non_whitelist_column_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """rows 含非白名单列时应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="wl-violate", fields_whitelist=["id", "name"])
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/wl-violate/rows",
        {"rows": [{"id": 50, "name": "X", "email": "leak@x.com"}]},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_write_unknown_column_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """rows 含表中不存在的列时应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="unknown-col")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/unknown-col/rows",
        {"rows": [{"id": 1, "nonexistent": "x"}]},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_write_empty_rows_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """rows 为空时应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": []},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_write_batch_over_limit_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """单批超过 1000 行应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    rows = [{"id": i, "name": f"u{i}"} for i in range(1001)]
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": rows},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_write_invalid_conflict_strategy_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """非法冲突策略应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": [{"id": 1, "name": "x"}], "conflict_strategy": "bogus"},
    )
    assert response.status_code == 400


# ================================================================
# 数据集/数据源状态
# ================================================================


@pytest.mark.django_db
def test_write_inactive_dataset_404(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """is_active=False 的数据集写入返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="inactive", is_active=False)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/inactive/rows",
        {"rows": [{"id": 1, "name": "x"}]},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_write_inactive_datasource_404(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """数据源 is_active=False 时写入返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    ds.is_active = False
    ds.save()
    _make_dataset(ds, slug="ds-inactive")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/ds-inactive/rows",
        {"rows": [{"id": 1, "name": "x"}]},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_write_dataset_not_found_404(
    make_user: Callable[..., User],
) -> None:
    """slug 不存在返回 404."""
    admin = make_user(username="admin", role=Role.ADMIN)
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/no-such/rows",
        {"rows": [{"id": 1, "name": "x"}]},
    )
    assert response.status_code == 404


# ================================================================
# 认证与 scope
# ================================================================


@pytest.mark.django_db
def test_write_no_token_401(tmp_path: Path) -> None:
    """无 Token 写入返回 401."""
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    client = Client()
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": [{"id": 1, "name": "x"}]},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_write_no_write_scope_403(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """Token 无 datasets:write scope 返回 403."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds)
    plaintext, _ = _make_token(admin, scopes=["datasets:read"])
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/write-target/rows",
        {"rows": [{"id": 1, "name": "x"}]},
    )
    assert response.status_code == 403


# ================================================================
# 限流与配额
# ================================================================


@pytest.mark.django_db
def test_write_rate_limited_429(
    make_user: Callable[..., User],
    tmp_path: Path,
    settings: Any,
) -> None:
    """超出每分钟限流阈值应 429."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="rl")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    settings.RATE_LIMIT_DATASET_WRITE = 2
    # 重置限流后端以应用新阈值
    rate_limiter.reset_rate_limiter()
    for _ in range(2):
        resp = _post(
            client,
            "/api/v1/datasets/rl/rows",
            {"rows": [{"id": 1, "name": "x"}]},
        )
        assert resp.status_code == 200
    # 第 3 次应被限流
    resp = _post(
        client,
        "/api/v1/datasets/rl/rows",
        {"rows": [{"id": 2, "name": "y"}]},
    )
    assert resp.status_code == 429
    assert resp["Retry-After"]


@pytest.mark.django_db
def test_write_quota_exceeded_429(
    make_user: Callable[..., User],
    tmp_path: Path,
    settings: Any,
) -> None:
    """超出每日配额应 429."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="quota")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    settings.DATASET_WRITE_DAILY_QUOTA = 5
    # 第一次写入 5 行，刚好用尽
    resp = _post(
        client,
        "/api/v1/datasets/quota/rows",
        {"rows": [{"id": i, "name": f"u{i}"} for i in range(100, 105)]},
    )
    assert resp.status_code == 200
    # 再写 1 行应超配额
    resp = _post(
        client,
        "/api/v1/datasets/quota/rows",
        {"rows": [{"id": 200, "name": "over"}]},
    )
    assert resp.status_code == 429


# ================================================================
# 幂等
# ================================================================


@pytest.mark.django_db
def test_write_idempotency_replay(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """相同 Idempotency-Key 重复请求应返回缓存结果."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="idem")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    body = {"rows": [{"id": 70, "name": "Idem"}]}
    resp1 = _post_idem(client, "/api/v1/datasets/idem/rows", body, "key-1")
    assert resp1.status_code == 200
    body1 = json.loads(resp1.content)
    assert body1["written"] == 1
    # 重复请求应返回缓存（written 仍为 1，不会因重复 UPSERT 再写一次）
    resp2 = _post_idem(client, "/api/v1/datasets/idem/rows", body, "key-1")
    assert resp2.status_code == 200
    assert json.loads(resp2.content) == body1


# ================================================================
# 审计日志
# ================================================================


@pytest.mark.django_db
def test_write_logs_audit(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """写入成功应记录 DATASET_WRITE 审计日志."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="audit-write")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    _post(
        client,
        "/api/v1/datasets/audit-write/rows",
        {"rows": [{"id": 80, "name": "Audited"}]},
    )
    log = AuditLog.objects.filter(
        action=AuditAction.DATASET_WRITE,
        source="business",
    ).first()
    assert log is not None
    assert log.resource_type == "dataset"
    assert log.extra.get("slug") == "audit-write"
    assert log.extra.get("strategy") == "upsert"
    assert log.extra.get("written") == 1


# ================================================================
# pk_fields 显式传入
# ================================================================


@pytest.mark.django_db
def test_write_explicit_pk_fields(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """显式传入 pk_fields 应使用其作为冲突判定列."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="explicit-pk")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/explicit-pk/rows",
        {
            "rows": [{"id": 90, "name": "Explicit"}],
            "pk_fields": ["id"],
        },
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["written"] == 1


@pytest.mark.django_db
def test_write_invalid_pk_fields_400(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """pk_fields 含不存在的列时应 400."""
    admin = make_user(username="admin", role=Role.ADMIN)
    ds = _make_sqlite_file_ds(tmp_path)
    _make_dataset(ds, slug="bad-pk")
    plaintext, _ = _make_token(admin)
    client = _token_client(plaintext)
    response = _post(
        client,
        "/api/v1/datasets/bad-pk/rows",
        {
            "rows": [{"id": 1, "name": "x"}],
            "pk_fields": ["nonexistent"],
        },
    )
    assert response.status_code == 400
