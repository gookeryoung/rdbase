"""健壮性模块端到端测试（E2E）.

验证 P8 健壮性栈在真实业务流中协同工作，而非孤立单元：

1. 健康检查返回聚合状态
2. 熔断器：连续失败驱动 → OPEN 拒绝 → 成功恢复
3. 分布式锁：并发同任务互斥（失败方返回 409）
4. 幂等：相同 Idempotency-Key 回放首次结果
5. 备份恢复：触发备份 → 任务创建 → 审计记录写入哈希链
6. 审计哈希链：贯穿全流程，verify_chain 校验整链无断点

本测试聚焦模块间协作语义，单模块细节由各模块专属测试覆盖。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.audit.audit import log_audit
from apps.audit.hashchain import verify_chain
from apps.audit.models import AuditAction, AuditLog
from apps.system import backup_service
from apps.system import circuit_breaker as cb_mod
from apps.system.circuit_breaker import CircuitOpenError, CircuitState, get_breaker
from apps.system.distributed_lock import get_lock
from apps.system.health import build_health
from apps.system.models import BackupTask
from django.http import HttpRequest
from django.test import Client


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _make_request(user: User, method: str = "POST", path: str = "/api/v1/test") -> HttpRequest:
    """构造带认证的 HttpRequest（供 log_audit / 幂等调用）."""
    req = HttpRequest()
    req.method = method
    req.path = path
    req.META = {"REMOTE_ADDR": "1.2.3.4", "HTTP_USER_AGENT": "pytest-e2e"}  # type: ignore[assignment]
    req.auth = user  # type: ignore[missing-attribute]
    return req


# ---------- 1. 健康检查 ----------


@pytest.mark.django_db
def test_e2e_health_check_returns_aggregated_status() -> None:
    """健康检查应返回包含全部组件的聚合状态."""
    result = build_health()
    assert result["project"] == "rdbase"
    assert "status" in result
    assert isinstance(result["components"], list)
    component_names = [c["name"] for c in result["components"]]
    # 至少含 DB 与磁盘检查器
    assert any("db" in n.lower() or "数据库" in n for n in component_names)
    assert any("disk" in n.lower() or "磁盘" in n for n in component_names)


# ---------- 2. 熔断器全生命周期 ----------


@pytest.mark.django_db
def test_e2e_circuit_breaker_trip_block_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    """熔断器：连续失败 → OPEN 拒绝 → HALF_OPEN 探测成功 → CLOSED 恢复."""
    cb_mod.reset_backend()
    breaker = get_breaker(
        "e2e:breaker:trip",
        config=cb_mod.CircuitBreakerConfig(failure_threshold=3, open_seconds=60),
    )
    # 连续失败 3 次触发熔断
    for _ in range(3):
        breaker.on_failure()
    assert breaker.state == CircuitState.OPEN

    # OPEN 状态下 before_call 应抛 CircuitOpenError
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    # 推进后端时钟超过 open_seconds，下次 before_call 转 HALF_OPEN 放行探测
    opened = breaker.opened_at
    monkeypatch.setattr(breaker._backend, "now", lambda: opened + 61)  # type: ignore[attr-defined]
    breaker.before_call()
    assert breaker.state == CircuitState.HALF_OPEN

    # 探测成功 → 恢复 CLOSED
    breaker.on_success()
    assert breaker.state == CircuitState.CLOSED


# ---------- 3. 分布式锁并发互斥 ----------


@pytest.mark.django_db
def test_e2e_distributed_lock_concurrent_mutex() -> None:
    """分布式锁：同任务并发触发，仅一个获取成功，其余失败."""
    lock_name = "e2e:lock:task:1"
    results: list[bool] = []
    lock = threading.Lock()  # 保护 results 列表

    def _try_acquire() -> bool:
        lock_inst = get_lock(lock_name)
        acquired = lock_inst.acquire()
        with lock:
            results.append(acquired)
        return acquired

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_try_acquire) for _ in range(5)]
        for f in futures:
            f.result()

    # 5 个并发，仅 1 个获取成功
    assert sum(results) == 1


# ---------- 4. 幂等缓存回放 ----------


@pytest.mark.django_db
def test_e2e_idempotency_replay(make_user: Callable[..., User]) -> None:
    """幂等：相同 Idempotency-Key 的重复请求回放首次结果."""
    from apps.system.idempotency import check_idempotency, release_idempotency, store_idempotency_result

    admin = make_user(role=Role.ADMIN)

    def _make_req(key: str) -> HttpRequest:
        req = _make_request(admin, path="/api/v1/sync/1/trigger")
        req.META["HTTP_IDEMPOTENCY_KEY"] = key  # type: ignore[assignment]
        return req

    # 首次请求：check 返回 None（继续执行业务）
    req1 = _make_req("e2e-key-1")
    assert check_idempotency(req1) is None
    store_idempotency_result(req1, 200, {"task_id": 99, "status": "ok"})

    # 重复请求：check 返回缓存响应
    req2 = _make_req("e2e-key-1")
    cached = check_idempotency(req2)
    assert cached is not None
    assert cached.status_code == 200
    # 释放幂等槽位（业务失败场景）
    release_idempotency(req2)


# ---------- 5. 备份触发 + 审计写入哈希链 ----------


@pytest.mark.django_db
def test_e2e_backup_creates_task_and_audit_hash(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备份触发：创建 BackupTask + 写 BACKUP_CREATE 审计记录（含哈希）."""

    def _noop_backup(task_id: int) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_backup", _noop_backup)
    admin = make_user(role=Role.ADMIN)
    before_tasks = BackupTask.objects.count()
    before_audit = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATE).count()

    client = Client()
    resp = client.post("/api/v1/system/backup", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"

    # BackupTask 已创建
    assert BackupTask.objects.count() == before_tasks + 1
    # 审计记录已写入且含哈希
    assert AuditLog.objects.filter(action=AuditAction.BACKUP_CREATE).count() == before_audit + 1
    log = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATE).order_by("-id").first()
    assert log is not None
    assert len(log.record_hash) == 64


# ---------- 6. 审计哈希链贯穿全流程 ----------


@pytest.mark.django_db
def test_e2e_audit_hash_chain_spans_full_flow(make_user: Callable[..., User]) -> None:
    """审计哈希链：多类操作写入后 verify_chain 校验整链无断点."""
    admin = make_user(role=Role.ADMIN)

    # 模拟业务流：依次写入不同类型的审计记录
    log_audit(_make_request(admin, path="/api/v1/datasources"), action=AuditAction.DATASOURCE_CREATE)
    log_audit(_make_request(admin, path="/api/v1/datasources/1"), action=AuditAction.DATASOURCE_UPDATE)
    log_audit(_make_request(admin, path="/api/v1/manager/rows"), action=AuditAction.DML_INSERT)
    log_audit(_make_request(admin, path="/api/v1/system/backup"), action=AuditAction.BACKUP_CREATE)
    log_audit(_make_request(admin, path="/api/v1/system/audit/verify"), action=AuditAction.AUDIT_VERIFY)

    # 校验整链无断点
    breaks = verify_chain()
    assert breaks == []

    # 所有记录的 record_hash 均为 64 字符
    for log in AuditLog.objects.exclude(record_hash=""):
        assert len(log.record_hash) == 64
        assert len(log.prev_hash) == 64 or log.prev_hash == ""


# ---------- 7. 审计校验 API 端到端 ----------


@pytest.mark.django_db
def test_e2e_audit_verify_endpoint(make_user: Callable[..., User]) -> None:
    """GET /system/audit/verify：返回 valid=true 且写入 AUDIT_VERIFY 审计."""
    admin = make_user(role=Role.ADMIN)
    # 预置一条审计记录
    log_audit(_make_request(admin, path="/api/v1/test"), action=AuditAction.WRITE)

    before_verify = AuditLog.objects.filter(action=AuditAction.AUDIT_VERIFY).count()
    client = Client()
    resp = client.get("/api/v1/system/audit/verify", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["total_records"] >= 1
    assert data["breaks"] == []
    # 校验操作本身也写入审计
    assert AuditLog.objects.filter(action=AuditAction.AUDIT_VERIFY).count() == before_verify + 1


# ---------- 8. 篡改后哈希链校验失败 ----------


@pytest.mark.django_db
def test_e2e_tamper_detected_by_verify_chain(make_user: Callable[..., User]) -> None:
    """篡改审计记录内容后 verify_chain 应检测到断点."""
    admin = make_user(role=Role.ADMIN)
    log_audit(_make_request(admin, path="/api/v1/a"), action=AuditAction.WRITE)
    r2 = log_audit(_make_request(admin, path="/api/v1/b"), action=AuditAction.WRITE)
    assert r2 is not None

    # 绕过 create_with_hash 直接篡改 r2 的 path
    AuditLog.objects.filter(pk=r2.pk).update(path="/api/v1/hacked")

    breaks = verify_chain()
    assert len(breaks) >= 1
    assert breaks[0].record_id == r2.pk


# ---------- 9. 健壮性状态 API 聚合查询 ----------


@pytest.mark.django_db
def test_e2e_system_status_apis_aggregated(make_user: Callable[..., User]) -> None:
    """管理员可聚合查询健康/熔断/锁/备份列表状态."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    headers = _auth(admin)

    # 健康检查
    resp = client.get("/api/v1/system/health", **headers)
    assert resp.status_code == 200
    # 熔断状态
    resp = client.get("/api/v1/system/circuit-states", **headers)
    assert resp.status_code == 200
    assert "items" in resp.json()
    # 锁状态
    resp = client.get("/api/v1/system/locks", **headers)
    assert resp.status_code == 200
    assert "items" in resp.json()
    # 备份列表（目录可能为空，但不报错）
    resp = client.get("/api/v1/system/backups", **headers)
    assert resp.status_code == 200
    assert "total" in resp.json()
