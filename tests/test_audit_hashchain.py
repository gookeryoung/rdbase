"""审计哈希链测试.

覆盖：

- compute_record_hash 确定性/首条空 prev_hash/不同 prev_hash
- create_with_hash 首条记录/链链接/哈希填充
- verify_chain 空表/完整链/篡改内容/篡改 hash/篡改 prev_hash
- create_with_hash 并发串行化
- log_audit / 中间件集成 create_with_hash
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from apps.accounts.models import Role, User
from apps.audit.audit import log_audit
from apps.audit.hashchain import ChainBreak, compute_record_hash, verify_chain
from apps.audit.models import AuditAction, AuditLog, AuditSource, AuditStatus
from django.http import HttpRequest

# ---------- compute_record_hash ----------


@pytest.mark.django_db
def test_compute_record_hash_deterministic(make_user: Callable[..., User]) -> None:
    """相同字段值 + 相同 prev_hash 应产生相同 hash."""
    log = AuditLog.objects.create_with_hash(username="alice", action=AuditAction.WRITE)
    h1 = compute_record_hash(log, log.prev_hash)
    h2 = compute_record_hash(log, log.prev_hash)
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.django_db
def test_compute_record_hash_first_record_empty_prev() -> None:
    """首条记录 prev_hash 为空字符串."""
    log = AuditLog.objects.create_with_hash(username="first", action=AuditAction.WRITE)
    assert log.prev_hash == ""
    assert log.record_hash != ""
    assert len(log.record_hash) == 64


@pytest.mark.django_db
def test_compute_record_hash_different_prev() -> None:
    """不同 prev_hash 产生不同 record_hash."""
    log = AuditLog.objects.create_with_hash(username="x", action=AuditAction.WRITE)
    h1 = compute_record_hash(log, "aaa")
    h2 = compute_record_hash(log, "bbb")
    assert h1 != h2


# ---------- create_with_hash ----------


@pytest.mark.django_db
def test_create_with_hash_chain_links() -> None:
    """第二条记录的 prev_hash 应等于第一条的 record_hash."""
    r1 = AuditLog.objects.create_with_hash(username="r1", action=AuditAction.WRITE)
    r2 = AuditLog.objects.create_with_hash(username="r2", action=AuditAction.WRITE)
    assert r1.prev_hash == ""
    assert r2.prev_hash == r1.record_hash
    assert r2.record_hash != r1.record_hash


@pytest.mark.django_db
def test_create_with_hash_populates_both_fields() -> None:
    """create_with_hash 应同时填充 prev_hash 与 record_hash."""
    AuditLog.objects.create_with_hash(username="a", action=AuditAction.WRITE)
    log = AuditLog.objects.create_with_hash(username="b", action=AuditAction.WRITE)
    log.refresh_from_db()
    assert log.prev_hash != ""
    assert log.record_hash != ""
    assert len(log.prev_hash) == 64
    assert len(log.record_hash) == 64


# ---------- verify_chain ----------


@pytest.mark.django_db
def test_verify_chain_empty() -> None:
    """空表校验返回空列表."""
    assert verify_chain() == []


@pytest.mark.django_db
def test_verify_chain_intact() -> None:
    """完整链校验通过（无断点）."""
    for i in range(5):
        AuditLog.objects.create_with_hash(username=f"user{i}", action=AuditAction.WRITE)
    breaks = verify_chain()
    assert breaks == []


@pytest.mark.django_db
def test_verify_chain_detects_tampered_content() -> None:
    """篡改记录内容后校验应检测到断点."""
    r1 = AuditLog.objects.create_with_hash(username="alice", action=AuditAction.WRITE)
    AuditLog.objects.create_with_hash(username="bob", action=AuditAction.WRITE)
    # 篡改 r1 的 username（绕过 create_with_hash 直接 update）
    AuditLog.objects.filter(pk=r1.pk).update(username="hacker")
    breaks = verify_chain()
    assert len(breaks) >= 1
    assert breaks[0].record_id == r1.pk


@pytest.mark.django_db
def test_verify_chain_detects_tampered_hash() -> None:
    """篡改 record_hash 后校验应检测到断点."""
    r1 = AuditLog.objects.create_with_hash(username="alice", action=AuditAction.WRITE)
    AuditLog.objects.create_with_hash(username="bob", action=AuditAction.WRITE)
    AuditLog.objects.filter(pk=r1.pk).update(record_hash="fake_hash_0000000000000000000000000000000000000")
    breaks = verify_chain()
    assert len(breaks) >= 1
    assert breaks[0].record_id == r1.pk


@pytest.mark.django_db
def test_verify_chain_detects_tampered_prev_hash() -> None:
    """篡改 prev_hash 后校验应检测到断点."""
    AuditLog.objects.create_with_hash(username="alice", action=AuditAction.WRITE)
    r2 = AuditLog.objects.create_with_hash(username="bob", action=AuditAction.WRITE)
    AuditLog.objects.filter(pk=r2.pk).update(prev_hash="tampered_prev_hash_000000000000000000000000000000")
    breaks = verify_chain()
    assert len(breaks) >= 1
    assert breaks[0].record_id == r2.pk


@pytest.mark.django_db
def test_verify_chain_returns_chain_break_dataclass() -> None:
    """ChainBreak 应包含 record_id/expected_hash/actual_hash/prev_hash."""
    r1 = AuditLog.objects.create_with_hash(username="alice", action=AuditAction.WRITE)
    AuditLog.objects.filter(pk=r1.pk).update(record_hash="wrong")
    breaks = verify_chain()
    assert len(breaks) == 1
    b = breaks[0]
    assert isinstance(b, ChainBreak)
    assert b.record_id == r1.pk
    assert len(b.expected_hash) == 64
    assert b.actual_hash == "wrong"


# ---------- 并发串行化 ----------


@pytest.mark.django_db
def test_create_with_hash_concurrent_serialized() -> None:
    """并发创建多条记录后链应完整（无断点）.

    SQLite 不支持真正的并发写入，用 threading.Lock 模拟 ``select_for_update``
    在 PostgreSQL 上的行锁串行化效果。
    """
    lock = threading.Lock()
    count = 10

    def _create(i: int) -> None:
        with lock:
            AuditLog.objects.create_with_hash(username=f"u{i}", action=AuditAction.WRITE)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_create, i) for i in range(count)]
        for f in futures:
            f.result()
    assert AuditLog.objects.count() == count
    breaks = verify_chain()
    assert breaks == []


@pytest.mark.django_db
def test_create_with_hash_concurrent_no_duplicate_prev() -> None:
    """并发创建时 prev_hash 不应重复（每条记录链接到不同的前驱）."""
    lock = threading.Lock()

    def _create(i: int) -> None:
        with lock:
            AuditLog.objects.create_with_hash(username=f"u{i}", action=AuditAction.WRITE)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_create, i) for i in range(8)]
        for f in futures:
            f.result()
    prev_hashes = list(AuditLog.objects.exclude(prev_hash="").values_list("prev_hash", flat=True))
    assert len(prev_hashes) == len(set(prev_hashes))


# ---------- log_audit / 中间件集成 ----------


@pytest.mark.django_db
def test_log_audit_integration_with_hash(make_user: Callable[..., User]) -> None:
    """log_audit 创建的记录应含 prev_hash 与 record_hash."""
    admin = make_user(role=Role.ADMIN)
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {"REMOTE_ADDR": "1.2.3.4", "HTTP_USER_AGENT": "pytest"}  # type: ignore[assignment]
    req.auth = admin  # type: ignore[missing-attribute]
    log = log_audit(req, action=AuditAction.DML_INSERT, sql="INSERT INTO t VALUES(1)")
    assert log is not None
    log.refresh_from_db()  # type: ignore[union-attr]
    # prev_hash 可能非空（若 DB 有其他记录），record_hash 必须为 64 字符
    assert len(log.record_hash) == 64  # type: ignore[union-attr]
    breaks = verify_chain()
    assert breaks == []


@pytest.mark.django_db
def test_middleware_integration_with_hash(make_user: Callable[..., User]) -> None:
    """中间件创建的记录应含 prev_hash 与 record_hash."""
    from apps.audit.middleware import _record_middleware_audit
    from django.http import HttpResponse

    admin = make_user(role=Role.ADMIN)
    req = HttpRequest()
    req.method = "POST"
    req.path = "/api/v1/test"
    req.META = {"REMOTE_ADDR": "1.2.3.4", "HTTP_USER_AGENT": "pytest"}  # type: ignore[assignment]
    req.auth = admin  # type: ignore[missing-attribute]
    resp = HttpResponse(status=201)
    _record_middleware_audit(req, resp, elapsed_ms=42)
    log = AuditLog.objects.get(source=AuditSource.MIDDLEWARE, path="/api/v1/test")
    assert len(log.record_hash) == 64
    assert log.status == AuditStatus.SUCCESS
    breaks = verify_chain()
    assert breaks == []


# ---------- 新增枚举 ----------


def test_audit_action_new_choices() -> None:
    """新增的 BACKUP_CREATE/BACKUP_RESTORE/AUDIT_VERIFY 枚举值正确."""
    assert AuditAction.BACKUP_CREATE == "backup.create"
    assert AuditAction.BACKUP_RESTORE == "backup.restore"
    assert AuditAction.AUDIT_VERIFY == "audit.verify"


# ---------- to_dict ----------


@pytest.mark.django_db
def test_chain_break_to_dict() -> None:
    """ChainBreak.to_dict 应返回 4 个字段."""
    r1 = AuditLog.objects.create_with_hash(username="x", action=AuditAction.WRITE)
    AuditLog.objects.filter(pk=r1.pk).update(record_hash="bad")
    breaks = verify_chain()
    d = breaks[0].to_dict()
    assert set(d.keys()) == {"record_id", "expected_hash", "actual_hash", "prev_hash"}
    assert d["record_id"] == r1.pk
    assert d["actual_hash"] == "bad"
