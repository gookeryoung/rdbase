"""健壮性压力测试.

标记 ``slow``，默认 ``make cov``（``-m "not slow"``）跳过，需显式运行：

    uv run pytest tests/test_robustness_stress.py -v -m slow

覆盖：

- 并发触发：多线程同任务触发，分布式锁保证仅一个执行，其余失败
- 限流边界：大量并发相同 Idempotency-Key，仅一个执行，其余命中 in_progress（409）或缓存回放
- 熔断短路：高并发连续失败触发熔断，后续请求被短路（CircuitOpenError）
- 锁竞争：高并发锁获取，成功数恒为 1
- 审计哈希链：大批量写入后链完整（SQLite 不支持并发写入，用串行模拟并发量）
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from apps.accounts.models import Role, User
from apps.system import circuit_breaker as cb_mod
from apps.system.circuit_breaker import CircuitOpenError, CircuitState, get_breaker
from apps.system.distributed_lock import get_lock
from apps.system.idempotency import check_idempotency, store_idempotency_result
from django.http import HttpRequest


def _make_req(user: User, key: str | None = None, path: str = "/api/v1/sync/1/trigger") -> HttpRequest:
    """构造带认证与可选幂等 key 的请求."""
    req = HttpRequest()
    req.method = "POST"
    req.path = path
    req.META = {"REMOTE_ADDR": "1.2.3.4", "HTTP_USER_AGENT": "pytest-stress"}  # type: ignore[assignment]
    req.auth = user  # type: ignore[missing-attribute]
    if key is not None:
        req.META["HTTP_IDEMPOTENCY_KEY"] = key  # type: ignore[assignment]
    return req


# ---------- 并发触发：分布式锁互斥 ----------


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_concurrent_lock_held_blocks_all() -> None:
    """持锁不释放时，并发竞争全部失败（仅首个成功）.

    模拟长任务执行中：首个请求获取锁后不释放，其余并发请求全部获取失败。
    锁后端为 Redis/本地内存，不涉及 DB 并发写入。
    """
    lock_name = "stress:lock:held:1"
    results: list[bool] = []
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(20)

    def _try_acquire() -> bool:
        start_barrier.wait()  # 确保所有线程同时开始竞争
        lock_inst = get_lock(lock_name)
        acquired = lock_inst.acquire()
        with results_lock:
            results.append(acquired)
        # 不释放，模拟长任务
        return acquired

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(_try_acquire) for _ in range(20)]
        for f in futures:
            f.result()

    assert sum(results) == 1  # 仅 1 个成功
    assert len(results) == 20


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_lock_release_allows_reuse() -> None:
    """持锁释放后，后续可再次获取（验证锁可重用，串行避免并发竞态）."""
    lock_name = "stress:lock:reuse:1"
    success = 0

    for _ in range(30):
        lock_inst = get_lock(lock_name)
        acquired = lock_inst.acquire()
        if acquired:
            success += 1
            lock_inst.release()

    # 串行获取-释放，每次都应成功
    assert success == 30


# ---------- 限流边界：幂等并发 ----------


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_idempotency_concurrent_same_key(make_user: Callable[..., User]) -> None:
    """大量并发相同 Idempotency-Key：仅一个执行，其余命中 in_progress（409）.

    首个请求获取 in_progress 槽位后执行业务，并发请求在业务完成前到达应抛 409。
    幂等存储为 Redis/本地内存，不涉及 DB 并发写入。
    """
    admin = make_user(role=Role.ADMIN)
    key = "stress-idem-key-1"
    executed_count = 0
    blocked_count = 0
    counter_lock = threading.Lock()
    business_started = threading.Event()
    business_allowed = threading.Event()

    def _hit_api() -> None:
        nonlocal executed_count, blocked_count
        req = _make_req(admin, key=key)
        try:
            result = check_idempotency(req)
        except Exception:
            # in_progress 阶段抛 HttpError(409)
            with counter_lock:
                blocked_count += 1
            return
        if result is not None:
            # 命中缓存（已完成）
            return
        # 首次请求，进入业务
        with counter_lock:
            executed_count += 1
        business_started.set()
        business_allowed.wait(timeout=2)  # 等待业务完成信号
        store_idempotency_result(req, 200, {"ok": True})

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_hit_api) for _ in range(10)]
        # 等待首个请求进入业务
        business_started.wait(timeout=2)
        # 释放业务，让首个请求完成
        business_allowed.set()
        for f in futures:
            f.result()

    # 首个请求执行业务，其余在 in_progress 阶段被阻断或命中缓存
    assert executed_count == 1
    assert executed_count + blocked_count <= 10


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_idempotency_replay_after_completion(make_user: Callable[..., User]) -> None:
    """业务完成后，并发重复请求全部命中缓存回放（无重复执行）."""
    admin = make_user(role=Role.ADMIN)
    key = "stress-idem-replay"
    req_init = _make_req(admin, key=key)
    # 首次执行
    assert check_idempotency(req_init) is None
    store_idempotency_result(req_init, 200, {"task_id": 42})

    hit_count = [0]
    counter_lock = threading.Lock()

    def _replay() -> int:
        req = _make_req(admin, key=key)
        result = check_idempotency(req)
        if result is not None:
            with counter_lock:
                hit_count[0] += 1
            return result.status_code
        return -1

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(_replay) for _ in range(20)]
        codes = [f.result() for f in futures]

    # 全部命中缓存，状态码 200，无重复执行
    assert all(c == 200 for c in codes)
    assert hit_count[0] == 20


# ---------- 熔断短路：高并发连续失败 ----------


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_circuit_breaker_short_circuit() -> None:
    """高并发连续失败触发熔断后，后续请求被短路（CircuitOpenError）.

    熔断器后端为 Redis/本地内存，不涉及 DB 并发写入。
    """
    cb_mod.reset_backend()
    breaker = get_breaker(
        "stress:breaker",
        config=cb_mod.CircuitBreakerConfig(failure_threshold=5, open_seconds=60),
    )

    # 并发上报失败（用 submit 避免参数传递问题）
    def _report_failure() -> None:
        breaker.on_failure()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_report_failure) for _ in range(20)]
        for f in futures:
            f.result()

    # 熔断器应已 OPEN
    assert breaker.state == CircuitState.OPEN

    # 后续并发请求全部被短路
    blocked = [0]
    counter_lock = threading.Lock()

    def _try_call() -> bool:
        try:
            breaker.before_call()
            return False
        except CircuitOpenError:
            with counter_lock:
                blocked[0] += 1
            return True

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_try_call) for _ in range(20)]
        for f in futures:
            f.result()

    assert blocked[0] == 20  # 全部被短路


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_circuit_breaker_threshold_boundary() -> None:
    """并发失败精确触发熔断：达阈值即转 OPEN."""
    cb_mod.reset_backend()
    breaker = get_breaker(
        "stress:breaker:threshold",
        config=cb_mod.CircuitBreakerConfig(failure_threshold=10, open_seconds=60),
    )

    def _fail() -> None:
        breaker.on_failure()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_fail) for _ in range(15)]
        for f in futures:
            f.result()

    # 15 次失败超过阈值 10，应已 OPEN
    assert breaker.state == CircuitState.OPEN


# ---------- 审计哈希链大批量写入 ----------


@pytest.mark.slow
@pytest.mark.django_db
def test_stress_audit_hashchain_bulk_writes(make_user: Callable[..., User]) -> None:
    """大批量审计写入后哈希链应完整（无断点）.

    SQLite 不支持并发写入，用串行写入模拟大批量场景（验证链增长后校验性能与完整性）。
    """
    from apps.audit.audit import log_audit
    from apps.audit.hashchain import verify_chain
    from apps.audit.models import AuditAction, AuditLog

    admin = make_user(role=Role.ADMIN)
    count = 50

    for i in range(count):
        req = HttpRequest()
        req.method = "POST"
        req.path = f"/api/v1/stress/{i}"
        req.META = {"REMOTE_ADDR": "1.2.3.4"}  # type: ignore[assignment]
        req.auth = admin  # type: ignore[missing-attribute]
        log_audit(req, action=AuditAction.WRITE, sql=f"-- stress {i}")

    assert AuditLog.objects.filter(action=AuditAction.WRITE).count() >= count
    breaks = verify_chain()
    assert breaks == []
    # 全部记录的 record_hash 为 64 字符
    for log in AuditLog.objects.exclude(record_hash=""):
        assert len(log.record_hash) == 64
