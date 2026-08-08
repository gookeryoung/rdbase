"""Redis 分布式锁.

防止同一任务（sync config / ingest task）被并发执行，req-03 第19行。

设计要点：
- 加锁：``SET key value NX EX ttl``，原子获取（value 为唯一 token）。
- 释放：Lua 脚本校验 value 后 DEL，防止误释放他人持有的锁。
- 锁超时 30s 自动释放（防进程崩溃后死锁），可配。
- Redis 不可用时降级为本地内存锁（单进程内互斥，记 WARNING）；strict 模式下拒绝。
  req-03 约束与风险第 7 条。
- 每次创建 :class:`DistributedLock` 实例独立持有 token，后端共享保证互斥。
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import redis

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 锁键前缀，与熔断器/幂等的命名空间隔离。
_KEY_PREFIX = "rdbase:lock"
# 默认锁超时（秒）：req-03 第19行要求 30s。
_DEFAULT_TTL = 30


@dataclass(frozen=True)
class LockConfig:
    """锁配置.

    Attributes:
        ttl_seconds: 锁超时秒数，到期自动释放。
        strict: True 时 Redis 不可用直接拒绝加锁；False 时降级为本地内存锁。
    """

    ttl_seconds: int = _DEFAULT_TTL
    strict: bool = False


DEFAULT_CONFIG = LockConfig()


class LockAcquireError(RuntimeError):
    """获取锁失败（已被他人持有）."""

    def __init__(self, name: str, ttl: int) -> None:
        super().__init__(f"锁 {name} 被占用，{ttl}s 后自动释放")
        self.name = name
        self.ttl = ttl


class LockUnavailableError(RuntimeError):
    """锁后端不可用且 strict=True 时抛出."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class LockInfo:
    """锁状态信息.

    Attributes:
        name: 锁名（不含前缀）。
        held: 是否被持有。
        ttl: 剩余秒数（held=False 时为 0）。
    """

    name: str
    held: bool
    ttl: int


class _LockBackend(Protocol):
    """锁后端协议."""

    def acquire(self, name: str, value: str, ttl: int) -> bool: ...

    def release(self, name: str, value: str) -> bool: ...

    def get_info(self, name: str) -> LockInfo: ...

    def list_names(self, pattern: str = "*") -> list[str]: ...

    def is_distributed(self) -> bool:
        """是否为跨进程分布式后端（Redis）；本地内存/无后端返回 False.

        strict 模式据此判断是否拒绝加锁。
        """
        ...


class _LocalBackend:
    """进程内本地内存锁后端.

    用 ``threading.Lock`` 保护 check-then-act，仅本进程生效。
    用 ``time.monotonic`` 计时（不受时钟回拨）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # name -> (value, expires_at_monotonic)
        self._holders: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _get_live_holder(self, name: str) -> tuple[str, float] | None:
        """读取并清理过期记录（调用方须持有 self._lock）."""
        entry = self._holders.get(name)
        if entry is None:
            return None
        _value, expires_at = entry
        if self._now() >= expires_at:
            self._holders.pop(name, None)
            return None
        return entry

    def acquire(self, name: str, value: str, ttl: int) -> bool:
        with self._lock:
            existing = self._get_live_holder(name)
            if existing is not None:
                return False
            self._holders[name] = (value, self._now() + ttl)
            return True

    def release(self, name: str, value: str) -> bool:
        with self._lock:
            entry = self._holders.get(name)
            if entry is None or entry[0] != value:
                return False
            self._holders.pop(name, None)
            return True

    def get_info(self, name: str) -> LockInfo:
        with self._lock:
            entry = self._get_live_holder(name)
            if entry is None:
                return LockInfo(name=name, held=False, ttl=0)
            remaining = max(0, int(entry[1] - self._now()))
            return LockInfo(name=name, held=True, ttl=remaining)

    def list_names(self, pattern: str = "*") -> list[str]:
        with self._lock:
            # 清理过期项后再列出
            now = self._now()
            expired = [k for k, (_, exp) in self._holders.items() if now >= exp]
            for k in expired:
                self._holders.pop(k, None)
            return sorted(fnmatch.filter(list(self._holders.keys()), pattern))

    def is_distributed(self) -> bool:
        return False


class _RedisBackend:
    """Redis 分布式锁后端.

    跨进程共享锁状态。``acquire`` 用 ``SET NX EX`` 原子获取；
    ``release`` 用 WATCH/MULTI/EXEC 校验 value 后 DEL，防误释放（兼容 fakeredis）。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, name: str) -> str:
        return f"{_KEY_PREFIX}:{name}"

    def acquire(self, name: str, value: str, ttl: int) -> bool:
        return bool(self._client.set(self._k(name), value, ex=ttl, nx=True))

    def release(self, name: str, value: str) -> bool:
        # WATCH/MULTI/EXEC 原子校验 value 后 DEL，与 Lua 脚本等价且兼容 fakeredis。
        key = self._k(name)
        with self._client.pipeline() as pipe:
            try:
                pipe.watch(key)
                current = pipe.get(key)
            except redis.WatchError:
                return False  # 被其他客户端修改，视为释放失败
            if current is None or current != value:
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(key)
            try:
                pipe.execute()
                return True
            except redis.WatchError:
                return False  # 极端竞态：WATCH 后被修改

    def get_info(self, name: str) -> LockInfo:
        key = self._k(name)
        ttl = self._client.ttl(key)
        # ttl 返回 -2 表示 key 不存在，-1 表示无 TTL；二者均视为未持有。
        if ttl is None or ttl < 0:
            return LockInfo(name=name, held=False, ttl=0)
        return LockInfo(name=name, held=True, ttl=int(ttl))

    def list_names(self, pattern: str = "*") -> list[str]:
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

    def is_distributed(self) -> bool:
        return True


# 模块级后端单例，用锁保护 check-then-act。
_backend: _LockBackend | None = None
_backend_lock = threading.Lock()


def _resolve_backend() -> _LockBackend:
    """解析后端：Redis 可用时用共享后端，否则本地内存."""
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        client = get_redis()
        if client is not None:
            _backend = _RedisBackend(client)
            logger.info("分布式锁后端：Redis 共享（多 worker 跨进程生效）")
        else:
            _backend = _LocalBackend()
            logger.warning("Redis 未配置，分布式锁降级为本地内存（仅本进程生效）")
        return _backend


def reset_backend() -> None:
    """重置后端单例（仅测试用）."""
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _backend_lock:
        _backend = None


class DistributedLock:
    """分布式锁实例.

    每次创建独立持有 token，后端共享保证互斥。支持上下文管理器用法。

    典型用法::

        lock = DistributedLock("sync:config:1")
        if not lock.acquire():
            raise HttpError(409, "任务执行中")
        try:
            do_work()
        finally:
            lock.release()

    或::

        with DistributedLock("ingest:task:2") as lock:
            do_work()  # 获取失败抛 LockAcquireError
    """

    def __init__(self, name: str, config: LockConfig | None = None, backend: _LockBackend | None = None) -> None:
        self.name = name
        self.config = config or DEFAULT_CONFIG
        self._backend = backend or _resolve_backend()
        self._value: str | None = None

    @property
    def held(self) -> bool:
        """当前实例是否持有锁."""
        return self._value is not None

    def acquire(self) -> bool:
        """获取锁（非阻塞）.

        - strict 模式：要求真正的分布式后端（Redis），本地内存/无后端均拒绝。
        - 非 strict 模式：Redis 不可用时降级为本地内存锁（单进程内互斥）。

        Returns:
            True 表示获取成功；False 表示锁被他人持有。

        Raises:
            LockUnavailableError: strict 模式下后端非分布式（Redis 不可用）。
        """
        if self._value is not None:
            return True  # 已持有
        backend = self._backend
        if self.config.strict and not backend.is_distributed():
            raise LockUnavailableError("Redis 不可用且 strict=True，拒绝加锁")
        value = uuid.uuid4().hex
        if backend.acquire(self.name, value, self.config.ttl_seconds):
            self._value = value
            if not backend.is_distributed():
                logger.warning("锁 %s 降级为本地内存（Redis 不可用，仅本进程生效）", self.name)
            return True
        return False

    def release(self) -> bool:
        """释放锁（仅当 value 匹配时）.

        Returns:
            True 表示释放成功或本实例未持有且后端也无人持有（幂等）；
            False 表示锁被他人持有（无权释放）。
        """
        if self._value is None:
            # 本实例未持有：后端也无人持有时幂等返回 True，他人持有时返回 False。
            return not self._backend.get_info(self.name).held
        value = self._value
        self._value = None
        return self._backend.release(self.name, value)

    def __enter__(self) -> DistributedLock:
        if not self.acquire():
            raise LockAcquireError(self.name, self.config.ttl_seconds)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def info(self) -> LockInfo:
        """查询锁状态（不依赖本实例是否持有）."""
        return self._backend.get_info(self.name)


def get_lock(name: str, config: LockConfig | None = None) -> DistributedLock:
    """创建分布式锁实例.

    每次返回新实例（持有独立 token），后端共享保证互斥。

    Args:
        name: 锁名（如 ``sync:config:1``）。
        config: 锁配置，None 用默认。

    Returns:
        DistributedLock 实例。
    """
    return DistributedLock(name, config)


def list_lock_info(pattern: str = "*") -> list[LockInfo]:
    """列出后端中所有匹配的锁信息（供 API 暴露）.

    Args:
        pattern: 锁名通配符（默认 ``*`` 全部）。

    Returns:
        按名称排序的 LockInfo 列表。
    """
    backend = _resolve_backend()
    names = backend.list_names(pattern)
    return [backend.get_info(n) for n in names]


__all__ = [
    "DEFAULT_CONFIG",
    "DistributedLock",
    "LockAcquireError",
    "LockConfig",
    "LockInfo",
    "LockUnavailableError",
    "get_lock",
    "list_lock_info",
    "reset_backend",
]
