"""幂等保护（Idempotency-Key）.

为 sync/ingest 触发接口提供幂等保证：客户端传入 ``Idempotency-Key`` 请求头，
首次请求正常执行并缓存结果 24h，重复请求直接返回缓存结果。

设计要点：
- 幂等 key 以「认证主体维度」抽象：``get_idempotent_subject(request)`` 返回
  当前认证主体标识（JWT 场景为 ``user:{pk}``，P9 API Token 落地后切为
  ``token:{prefix}``），无需重构幂等层。req-03 关键决策第 2 条。
- key 格式：``rdbase:idem:{subject}:{idempotency_key}``，TTL 24h（可配）。
- 缓存结果包含 status_code 与 body（JSON 字符串），重复请求原样回放。
- 并发请求（首请求执行中，重复请求到达）通过 ``in_progress`` 标记返回 409，
  避免重复执行；``in_progress`` 标记 TTL 5 分钟（防进程崩溃后死锁）。
- Redis 不可用时降级为本地内存（仅本进程幂等有效），并记 WARNING。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import redis
from django.http import HttpRequest, HttpResponse

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 键前缀，与熔断器/锁的命名空间隔离。
_KEY_PREFIX = "rdbase:idem"
# 缓存结果 TTL：req-03 第19行要求 24h。
_DEFAULT_TTL = 24 * 60 * 60
# in_progress 标记 TTL：防进程崩溃后死锁，执行超过 5 分钟视为失败可重试。
_IN_PROGRESS_TTL = 5 * 60
# 默认请求头名（RFC 草案惯用形式）。
_HEADER = "Idempotency-Key"


class IdempotencyState(str, Enum):
    """幂等槽位状态."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True)
class IdempotencyConfig:
    """幂等配置.

    Attributes:
        ttl_seconds: 已完成结果缓存时长（秒）。
        in_progress_ttl_seconds: 执行中标记的存活时长（秒），超时自动释放便于重试。
        header_name: 请求头名称。
    """

    ttl_seconds: int = _DEFAULT_TTL
    in_progress_ttl_seconds: int = _IN_PROGRESS_TTL
    header_name: str = _HEADER


DEFAULT_CONFIG = IdempotencyConfig()


@dataclass
class IdempotencyRecord:
    """幂等缓存记录.

    Attributes:
        state: 槽位状态（in_progress/completed）。
        status_code: 业务响应状态码（in_progress 时为 0）。
        body: 业务响应体 JSON 字符串（in_progress 时为空）。
        created_at: 记录创建时间戳（unix ts）。
    """

    state: IdempotencyState
    status_code: int
    body: str
    created_at: float


def get_idempotent_subject(request: HttpRequest) -> str | None:
    """获取幂等主体标识.

    按认证方式区分主体：

    - **API Token 认证**（P9 落地）：``request.api_token`` 由 ``ApiTokenAuth``
      设置，返回 ``token:{prefix}``，使不同 Token 的幂等 key 隔离，
      同一 Token 的重复请求命中缓存。req-03 关键决策第 2 条。
    - **JWT 认证**（Web 前端会话）：从 ``request.auth`` 取 user_id，
      返回 ``user:{pk}``。
    - 未认证返回 None。

    Returns:
        主体标识字符串（如 ``user:1`` 或 ``token:aB3xK9pQ``）；未认证返回 None。
    """
    token = getattr(request, "api_token", None)
    if token is not None:
        # 仅 ApiToken 实例作为幂等主体（避免 MagicMock 等测试替身误判）
        from apps.accounts.models import ApiToken

        if isinstance(token, ApiToken):
            return f"token:{token.prefix}"
    user = getattr(request, "auth", None)
    pk = getattr(user, "pk", None)
    if pk is None:
        return None
    return f"user:{pk}"


def get_idempotency_key(request: HttpRequest, header_name: str = _HEADER) -> str | None:
    """从请求头提取 Idempotency-Key.

    Returns:
        key 字符串；未传头返回 None。
    """
    raw = request.headers.get(header_name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


class _Store(Protocol):
    """幂等存储后端协议."""

    def get(self, key: str) -> IdempotencyRecord | None: ...

    def acquire(self, key: str, ttl: int) -> bool: ...

    def store_result(self, key: str, status_code: int, body: str, ttl: int) -> None: ...

    def release(self, key: str) -> None: ...

    def list_keys(self, pattern: str = "*") -> list[str]: ...


def _encode(record: IdempotencyRecord) -> str:
    """将记录序列化为 JSON 字符串."""
    return json.dumps(
        {
            "state": record.state.value,
            "status_code": record.status_code,
            "body": record.body,
            "created_at": record.created_at,
        },
        ensure_ascii=False,
    )


def _decode(raw: str, key: str) -> IdempotencyRecord | None:
    """从 JSON 字符串反序列化记录；非法时记 WARNING 返回 None."""
    try:
        data = json.loads(raw)
        return IdempotencyRecord(
            state=IdempotencyState(data["state"]),
            status_code=int(data["status_code"]),
            body=str(data["body"]),
            created_at=float(data["created_at"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        logger.warning("幂等缓存 %s 内容非法: %r", key, raw, exc_info=True)
        return None


class _LocalStore:
    """进程内本地内存后端.

    用 ``threading.Lock`` 保护 check-then-act，适用于单 worker 场景。
    Redis 未配置时降级使用，幂等语义仅在本进程生效。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (record, expires_at_unix_ts)
        self._records: dict[str, tuple[IdempotencyRecord, float]] = {}

    @staticmethod
    def _now() -> float:
        return time.time()

    def _get_live(self, key: str) -> IdempotencyRecord | None:
        """读取并清理过期记录（调用方须持有 self._lock）."""
        entry = self._records.get(key)
        if entry is None:
            return None
        record, expires_at = entry
        if self._now() >= expires_at:
            self._records.pop(key, None)
            return None
        return record

    def get(self, key: str) -> IdempotencyRecord | None:
        with self._lock:
            return self._get_live(key)

    def acquire(self, key: str, ttl: int) -> bool:
        with self._lock:
            existing = self._get_live(key)
            if existing is not None:
                return False
            now = self._now()
            record = IdempotencyRecord(
                state=IdempotencyState.IN_PROGRESS,
                status_code=0,
                body="",
                created_at=now,
            )
            self._records[key] = (record, now + ttl)
            return True

    def store_result(self, key: str, status_code: int, body: str, ttl: int) -> None:
        with self._lock:
            now = self._now()
            record = IdempotencyRecord(
                state=IdempotencyState.COMPLETED,
                status_code=status_code,
                body=body,
                created_at=now,
            )
            self._records[key] = (record, now + ttl)

    def release(self, key: str) -> None:
        with self._lock:
            self._records.pop(key, None)

    def list_keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        with self._lock:
            # 清理过期项后再列出
            now = self._now()
            expired = [k for k, (_, exp) in self._records.items() if now >= exp]
            for k in expired:
                self._records.pop(k, None)
            return sorted(fnmatch.filter(list(self._records.keys()), pattern))


class _RedisStore:
    """Redis 共享后端.

    跨进程共享幂等状态，适用于多 worker 部署。
    ``acquire`` 用 ``SET NX EX`` 原子获取；``store_result`` 覆写 in_progress 标记。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, key: str) -> str:
        return f"{_KEY_PREFIX}:{key}"

    def get(self, key: str) -> IdempotencyRecord | None:
        redis_key = self._k(key)
        raw = self._client.get(redis_key)
        if raw is None:
            return None
        record = _decode(str(raw), key)
        if record is None:
            # 缓存损坏：删除 key，允许后续 acquire 成功（而非被 SET NX 阻塞）。
            self._client.delete(redis_key)
        return record

    def acquire(self, key: str, ttl: int) -> bool:
        record = IdempotencyRecord(
            state=IdempotencyState.IN_PROGRESS,
            status_code=0,
            body="",
            created_at=time.time(),
        )
        # SET NX EX：仅当 key 不存在时设置，原子获取槽位。
        return bool(self._client.set(self._k(key), _encode(record), ex=ttl, nx=True))

    def store_result(self, key: str, status_code: int, body: str, ttl: int) -> None:
        record = IdempotencyRecord(
            state=IdempotencyState.COMPLETED,
            status_code=status_code,
            body=body,
            created_at=time.time(),
        )
        # 覆写已存在的 in_progress 标记为最终结果，刷新 TTL。
        self._client.set(self._k(key), _encode(record), ex=ttl)

    def release(self, key: str) -> None:
        self._client.delete(self._k(key))

    def list_keys(self, pattern: str = "*") -> list[str]:
        # SCAN 避免 KEYS 阻塞 Redis。
        keys: list[str] = []
        prefix = self._k("")
        full_pattern = self._k(pattern)
        cursor = 0
        while True:
            cursor, batch = self._client.scan(cursor=cursor, match=full_pattern, count=100)
            for k in batch:
                if k.startswith(prefix):
                    keys.append(k[len(prefix) :])
            if cursor == 0:
                break
        return sorted(keys)


# 模块级后端单例，用锁保护 check-then-act。
_store: _Store | None = None
_store_lock = threading.Lock()


def _resolve_store() -> _Store:
    """解析后端：Redis 可用时用共享后端，否则本地内存."""
    global _store  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        client = get_redis()
        if client is not None:
            _store = _RedisStore(client)
            logger.info("幂等后端：Redis 共享（多 worker 跨进程生效）")
        else:
            _store = _LocalStore()
            logger.warning("Redis 未配置，幂等保护降级为本地内存（仅本进程生效）")
        return _store


def reset_store() -> None:
    """重置后端单例（仅测试用）.

    清空缓存的 store 实例，下次调用重新按 settings 解析后端。
    """
    global _store  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _store_lock:
        _store = None


def _build_key(subject: str, idempotency_key: str) -> str:
    """构造幂等缓存 key（subject 与 idempotency_key 拼接）."""
    return f"{subject}:{idempotency_key}"


class IdempotencyManager:
    """幂等管理器.

    封装幂等槽位的获取、结果存储与释放。线程安全（后端负责并发控制）。

    典型用法::

        subject = get_idempotent_subject(request)
        key = get_idempotency_key(request)
        if subject and key:
            record, should_run = manager.acquire(subject, key)
            if not should_run:
                if record.state == IdempotencyState.COMPLETED:
                    return cached_response(record)
                raise HttpError(409, "请求执行中")
        try:
            result = business()
            manager.store_result(subject, key, 200, json.dumps(result))
            return result
        except Exception:
            manager.release(subject, key)
            raise
    """

    def __init__(self, config: IdempotencyConfig | None = None, store: _Store | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._store = store or _resolve_store()

    def acquire(self, subject: str, idempotency_key: str) -> tuple[IdempotencyRecord | None, bool]:
        """获取幂等槽位.

        Args:
            subject: 认证主体标识（如 ``user:1``）。
            idempotency_key: 客户端传入的 Idempotency-Key。

        Returns:
            元组 ``(record, should_run)``：
            - 命中已存在记录（COMPLETED 或 IN_PROGRESS）：``(record, False)``，调用方应回放缓存或返回 409。
            - 首次获取成功：``(None, True)``，调用方应执行业务逻辑。
        """
        key = _build_key(subject, idempotency_key)
        existing = self._store.get(key)
        if existing is not None:
            return existing, False
        if self._store.acquire(key, self.config.in_progress_ttl_seconds):
            return None, True
        # 并发获取失败（极小窗口）：再次读取，必然命中。
        existing = self._store.get(key)
        return existing, False

    def store_result(self, subject: str, idempotency_key: str, status_code: int, body: str) -> None:
        """存储幂等结果（业务成功后调用）.

        Args:
            subject: 认证主体标识。
            idempotency_key: 客户端传入的 Idempotency-Key。
            status_code: 业务响应状态码。
            body: 业务响应体 JSON 字符串。
        """
        key = _build_key(subject, idempotency_key)
        self._store.store_result(key, status_code, body, self.config.ttl_seconds)

    def release(self, subject: str, idempotency_key: str) -> None:
        """释放幂等槽位（业务失败时调用，允许后续重试）."""
        key = _build_key(subject, idempotency_key)
        self._store.release(key)

    def get(self, subject: str, idempotency_key: str) -> IdempotencyRecord | None:
        """查询幂等记录（调试/测试用）."""
        key = _build_key(subject, idempotency_key)
        return self._store.get(key)

    def list_keys(self, pattern: str = "*") -> list[str]:
        """列出匹配的幂等 key（调试/测试用）."""
        return self._store.list_keys(pattern)


# 模块级 manager 单例。
_manager: IdempotencyManager | None = None
_manager_lock = threading.Lock()


def get_manager(config: IdempotencyConfig | None = None) -> IdempotencyManager:
    """获取或创建幂等管理器单例.

    Args:
        config: 配置，None 用默认。仅首次创建时生效。

    Returns:
        IdempotencyManager 单例。
    """
    global _manager  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is not None:
            return _manager
        _manager = IdempotencyManager(config)
        return _manager


def reset_manager() -> None:
    """重置 manager 单例（仅测试用）."""
    global _manager  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _manager_lock:
        _manager = None


# ================================================================
# 请求级便捷函数（供 sync/ingest 触发端点复用）
# ================================================================


def check_idempotency(request: HttpRequest) -> HttpResponse | None:
    """检查幂等缓存，命中则返回缓存响应，未命中或无 key 返回 None.

    供 sync/ingest 触发端点在执行业务前调用：

    - 请求未携带 Idempotency-Key 或未认证：返回 None（继续执行业务）。
    - 命中已完成的缓存记录：返回 ``JsonResponse`` 回放首次结果。
    - 命中 in_progress 标记：抛 ``HttpError(409)`` 提示请求执行中。
    - 首次请求：获取 in_progress 槽位后返回 None（继续执行业务），
      调用方应在业务成功后调用 :func:`store_idempotency_result`，
      业务失败时调用 :func:`release_idempotency` 释放槽位允许重试。

    Args:
        request: HTTP 请求。

    Returns:
        命中缓存时返回 ``JsonResponse``；否则返回 None。

    Raises:
        HttpError(409): 相同 Idempotency-Key 请求正在执行中。
    """
    from django.http import JsonResponse
    from ninja.errors import HttpError

    subject = get_idempotent_subject(request)
    key = get_idempotency_key(request)
    if not subject or not key:
        return None
    manager = get_manager()
    record, should_run = manager.acquire(subject, key)
    if should_run:
        return None
    if record is None:
        # 极小竞态窗口：获取失败但读取为空，按 in_progress 处理。
        raise HttpError(409, "相同 Idempotency-Key 请求正在执行中")
    if record.state == IdempotencyState.COMPLETED:
        try:
            body = json.loads(record.body)
        except json.JSONDecodeError:
            # 缓存体损坏：释放后让调用方重新执行。
            manager.release(subject, key)
            logger.warning("幂等缓存 %s:%s body 损坏，已释放", subject, key)
            return None
        return JsonResponse(body, status=record.status_code)
    # IN_PROGRESS
    raise HttpError(409, "相同 Idempotency-Key 请求正在执行中")


def store_idempotency_result(request: HttpRequest, status_code: int, body: Any) -> None:
    """存储幂等结果（业务成功后调用）.

    仅当请求携带 Idempotency-Key 时生效，否则空操作。

    Args:
        request: HTTP 请求。
        status_code: 业务响应状态码。
        body: 业务响应体（将被 JSON 序列化）。
    """
    subject = get_idempotent_subject(request)
    key = get_idempotency_key(request)
    if not subject or not key:
        return
    manager = get_manager()
    manager.store_result(subject, key, status_code, json.dumps(body, ensure_ascii=False, default=str))


def release_idempotency(request: HttpRequest) -> None:
    """释放幂等槽位（业务失败时调用，允许后续重试）.

    仅当请求携带 Idempotency-Key 时生效。
    """
    subject = get_idempotent_subject(request)
    key = get_idempotency_key(request)
    if not subject or not key:
        return
    manager = get_manager()
    manager.release(subject, key)


__all__ = [
    "DEFAULT_CONFIG",
    "IdempotencyConfig",
    "IdempotencyManager",
    "IdempotencyRecord",
    "IdempotencyState",
    "check_idempotency",
    "get_idempotency_key",
    "get_idempotent_subject",
    "get_manager",
    "release_idempotency",
    "reset_manager",
    "reset_store",
    "store_idempotency_result",
]
