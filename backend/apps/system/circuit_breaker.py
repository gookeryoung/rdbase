"""熔断器（Circuit Breaker）.

为外部数据源调用（sync/ingest 写操作、连接探测）提供熔断保护，避免下游故障时
持续打满请求导致级联雪崩。

三态状态机：
- CLOSED：正常放行；连续失败次数达 ``failure_threshold`` 转 OPEN。
- OPEN：直接拒绝调用（抛 :class:`CircuitOpenError`）；保持 ``open_seconds`` 后
  转 HALF_OPEN 放行少量探测。
- HALF_OPEN：限制 ``half_open_max_calls`` 次探测调用；任一成功转 CLOSED，
  任一失败立即转 OPEN。

共享后端：
- 单 worker 进程内用本地内存（``_LocalBackend``）。
- 多 worker 跨进程共享用 Redis（``_RedisBackend``），键空间 ``rdbase:cb:{name}:*``。
  Redis 未配置时降级为本地内存（仅保护本进程），并记 WARNING。

设计要点：
- 失败计数为「连续失败次数」，成功即清零（CLOSED/HALF_OPEN 均如此）。
- HALF_OPEN 探测调用计数在转 OPEN/CLOSED 时清零。
- 时间源：本地后端用 ``time.monotonic``（不受时钟回拨），Redis 后端用
  ``time.time``（unix ts，跨进程一致）。breaker 通过 ``backend.now()`` 取时间，
  避免本地与共享后端时钟语义不一致。
- 后端操作非原子（多 key 读改写）存在轻微竞态，熔断语义容忍（少计一两次失败
  不会破坏整体保护意图），故未引入 Lua 脚本。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import redis

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis 键前缀，与 P8/P9 其它 Redis 用途隔离命名空间。
_KEY_PREFIX = "rdbase:cb"


class CircuitState(str, Enum):
    """熔断器三态."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """熔断器处于 OPEN 状态时调用被拒绝.

    调用方捕获后应直接返回失败或 503，不应再次触发下游调用。
    """

    def __init__(self, name: str, state: CircuitState, retry_after: float) -> None:
        super().__init__(f"熔断器 {name} 处于 {state.value} 状态，{retry_after:.1f}s 后可重试")
        self.name = name
        self.state = state
        self.retry_after = retry_after


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """熔断器配置.

    Attributes:
        failure_threshold: CLOSED 状态下连续失败多少次触发熔断（转 OPEN）。
        open_seconds: OPEN 状态保持时长（秒），到期转 HALF_OPEN 探测。
        half_open_max_calls: HALF_OPEN 状态允许的探测调用上限。
    """

    failure_threshold: int = 5
    open_seconds: float = 60.0
    half_open_max_calls: int = 3


# 默认配置（req-03 关键决策第 7 条）。
DEFAULT_CONFIG = CircuitBreakerConfig()


class _Backend(Protocol):
    """熔断器状态后端协议."""

    def get_state(self, name: str) -> CircuitState: ...

    def get_failure_count(self, name: str) -> int: ...

    def get_opened_at(self, name: str) -> float: ...

    def get_half_open_calls(self, name: str) -> int: ...

    def set_state(self, name: str, state: CircuitState) -> None: ...

    def incr_failure(self, name: str) -> int: ...

    def reset_failure(self, name: str) -> None: ...

    def set_opened_at(self, name: str, ts: float) -> None: ...

    def incr_half_open_call(self, name: str) -> int: ...

    def reset_half_open_calls(self, name: str) -> None: ...

    def now(self) -> float: ...


class _LocalBackend:
    """进程内本地内存后端.

    用 ``threading.Lock`` 保护 check-then-act，适用于单 worker 场景。
    多 worker 时各进程独立维护状态，熔断语义仅在本进程生效。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, CircuitState] = {}
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._half_open_calls: dict[str, int] = {}

    def get_state(self, name: str) -> CircuitState:
        with self._lock:
            return self._states.get(name, CircuitState.CLOSED)

    def get_failure_count(self, name: str) -> int:
        with self._lock:
            return self._failures.get(name, 0)

    def get_opened_at(self, name: str) -> float:
        with self._lock:
            return self._opened_at.get(name, 0.0)

    def get_half_open_calls(self, name: str) -> int:
        with self._lock:
            return self._half_open_calls.get(name, 0)

    def set_state(self, name: str, state: CircuitState) -> None:
        with self._lock:
            self._states[name] = state

    def incr_failure(self, name: str) -> int:
        with self._lock:
            self._failures[name] = self._failures.get(name, 0) + 1
            return self._failures[name]

    def reset_failure(self, name: str) -> None:
        with self._lock:
            self._failures.pop(name, None)

    def set_opened_at(self, name: str, ts: float) -> None:
        with self._lock:
            self._opened_at[name] = ts

    def incr_half_open_call(self, name: str) -> int:
        with self._lock:
            self._half_open_calls[name] = self._half_open_calls.get(name, 0) + 1
            return self._half_open_calls[name]

    def reset_half_open_calls(self, name: str) -> None:
        with self._lock:
            self._half_open_calls.pop(name, None)

    def now(self) -> float:
        # 本地用 monotonic：不受系统时钟回拨影响，单进程内 open_seconds 判定稳定。
        return time.monotonic()


class _RedisBackend:
    """Redis 共享后端.

    跨进程共享熔断状态，适用于多 worker 部署。各字段独立 key 存储：
    - ``{prefix}:{name}:state``
    - ``{prefix}:{name}:failures``
    - ``{prefix}:{name}:opened_at``
    - ``{prefix}:{name}:half_open_calls``

    多 key 读改写非原子，存在轻微竞态；熔断语义容忍（少计一两次失败不破坏整体保护）。
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _k(self, name: str, field: str) -> str:
        return f"{_KEY_PREFIX}:{name}:{field}"

    def get_state(self, name: str) -> CircuitState:
        raw = self._client.get(self._k(name, "state"))
        if raw is None:
            return CircuitState.CLOSED
        try:
            return CircuitState(str(raw))
        except ValueError:
            logger.warning("熔断器 %s 状态值非法: %r，回退 CLOSED", name, raw)
            return CircuitState.CLOSED

    def get_failure_count(self, name: str) -> int:
        raw = self._client.get(self._k(name, "failures"))
        return int(raw) if raw is not None else 0

    def get_opened_at(self, name: str) -> float:
        raw = self._client.get(self._k(name, "opened_at"))
        return float(raw) if raw is not None else 0.0

    def get_half_open_calls(self, name: str) -> int:
        raw = self._client.get(self._k(name, "half_open_calls"))
        return int(raw) if raw is not None else 0

    def set_state(self, name: str, state: CircuitState) -> None:
        # state 无需 TTL（短字符串），由 open_seconds 驱动状态迁移而非 key 过期。
        self._client.set(self._k(name, "state"), state.value)

    def incr_failure(self, name: str) -> int:
        return int(self._client.incr(self._k(name, "failures")))

    def reset_failure(self, name: str) -> None:
        self._client.delete(self._k(name, "failures"))

    def set_opened_at(self, name: str, ts: float) -> None:
        # opened_at 与 state 同生命周期，无 TTL；状态迁移时被读取后覆写。
        self._client.set(self._k(name, "opened_at"), str(ts))

    def incr_half_open_call(self, name: str) -> int:
        return int(self._client.incr(self._k(name, "half_open_calls")))

    def reset_half_open_calls(self, name: str) -> None:
        self._client.delete(self._k(name, "half_open_calls"))

    def now(self) -> float:
        # Redis 共享后端用 unix ts：跨进程一致，所有 worker 对 opened_at 判定同步。
        return time.time()


# 模块级后端单例与已注册 breaker 缓存，用锁保护 check-then-act。
_backend: _Backend | None = None
_backend_lock = threading.Lock()
_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def _resolve_backend() -> _Backend:
    """解析后端：Redis 可用时用共享后端，否则本地内存.

    首次调用时确定后端并缓存；Redis 未配置时记 WARNING 提示仅本进程生效。
    """
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        client = get_redis()
        if client is not None:
            _backend = _RedisBackend(client)
            logger.info("熔断器后端：Redis 共享（多 worker 跨进程生效）")
        else:
            _backend = _LocalBackend()
            logger.warning("Redis 未配置，熔断器降级为本地内存（仅本进程生效）")
        return _backend


def reset_backend() -> None:
    """重置后端单例与 breaker 缓存（仅测试用）."""
    global _backend  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _backend_lock:
        _backend = None
    with _breakers_lock:
        _breakers.clear()


class CircuitBreaker:
    """熔断器实例.

    每个 ``name`` 对应一个 breaker，通过 :func:`get_breaker` 获取单例。
    状态持久化在后端（本地内存或 Redis），breaker 实例本身无状态，便于跨调用复用。

    典型用法::

        breaker = get_breaker("sync:config:1")
        breaker.before_call()  # OPEN 时抛 CircuitOpenError
        try:
            result = do_risky_call()
            breaker.on_success()
            return result
        except Exception as exc:
            breaker.on_failure()
            raise
    """

    def __init__(self, name: str, config: CircuitBreakerConfig, backend: _Backend) -> None:
        self.name = name
        self.config = config
        self._backend = backend

    # ----------------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """当前状态（每次读取从后端取最新值）."""
        return self._backend.get_state(self.name)

    @property
    def failure_count(self) -> int:
        """当前连续失败次数."""
        return self._backend.get_failure_count(self.name)

    @property
    def opened_at(self) -> float:
        """最近一次进入 OPEN 的时间戳（monotonic 或 unix ts）."""
        return self._backend.get_opened_at(self.name)

    @property
    def half_open_calls(self) -> int:
        """HALF_OPEN 状态下已放行的探测调用数."""
        return self._backend.get_half_open_calls(self.name)

    def snapshot(self) -> dict[str, Any]:
        """返回可序列化的状态快照（供 API 暴露）."""
        current = self.state
        now = self._backend.now()
        elapsed = now - self.opened_at if current != CircuitState.CLOSED else 0.0
        retry_after = max(0.0, self.config.open_seconds - elapsed) if current == CircuitState.OPEN else 0.0
        return {
            "name": self.name,
            "state": current.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.config.failure_threshold,
            "opened_at": self.opened_at,
            "open_seconds": self.config.open_seconds,
            "half_open_calls": self.half_open_calls,
            "half_open_max_calls": self.config.half_open_max_calls,
            "retry_after": round(retry_after, 1),
        }

    # ----------------------------------------------------------------
    # 状态机驱动
    # ----------------------------------------------------------------

    def before_call(self) -> None:
        """调用前检查：OPEN 时抛 :class:`CircuitOpenError`.

        - CLOSED：直接放行。
        - OPEN：检查是否到 ``open_seconds``，到期转 HALF_OPEN 并放行首个探测；
          未到期抛异常。
        - HALF_OPEN：检查探测调用数是否达上限，达上限抛异常，否则放行并计数 +1。
        """
        current = self._backend.get_state(self.name)
        if current == CircuitState.CLOSED:
            return
        if current == CircuitState.OPEN:
            self._maybe_open_to_half_open()
            # 转换后再次读取状态：仍在 OPEN 则拒绝，已转 HALF_OPEN 则放行。
            current = self._backend.get_state(self.name)
            if current == CircuitState.OPEN:
                elapsed = self._backend.now() - self._backend.get_opened_at(self.name)
                retry_after = max(0.0, self.config.open_seconds - elapsed)
                raise CircuitOpenError(self.name, CircuitState.OPEN, retry_after)
            # 已转 HALF_OPEN，落到下面分支放行探测。
            current = CircuitState.HALF_OPEN
        if current == CircuitState.HALF_OPEN:
            calls = self._backend.incr_half_open_call(self.name)
            if calls > self.config.half_open_max_calls:
                # 探测已满：拒绝本次调用（计数已 +1，转回 OPEN 时会被清零）。
                raise CircuitOpenError(self.name, CircuitState.HALF_OPEN, self.config.open_seconds)

    def _maybe_open_to_half_open(self) -> None:
        """OPEN 到期则转 HALF_OPEN（重置探测计数）."""
        opened_at = self._backend.get_opened_at(self.name)
        if self._backend.now() - opened_at >= self.config.open_seconds:
            self._backend.set_state(self.name, CircuitState.HALF_OPEN)
            self._backend.reset_half_open_calls(self.name)
            logger.info("熔断器 %s OPEN -> HALF_OPEN（开放探测）", self.name)

    def on_success(self) -> None:
        """调用成功：CLOSED 重置失败计数；HALF_OPEN 转 CLOSED（恢复）."""
        current = self._backend.get_state(self.name)
        if current == CircuitState.HALF_OPEN:
            self._backend.set_state(self.name, CircuitState.CLOSED)
            self._backend.reset_failure(self.name)
            self._backend.reset_half_open_calls(self.name)
            logger.info("熔断器 %s HALF_OPEN -> CLOSED（探测成功，恢复）", self.name)
        elif current == CircuitState.CLOSED:
            self._backend.reset_failure(self.name)

    def on_failure(self) -> None:
        """调用失败：CLOSED 累加失败计数，达阈值转 OPEN；HALF_OPEN 直接转 OPEN."""
        current = self._backend.get_state(self.name)
        if current == CircuitState.HALF_OPEN:
            # 探测失败：立即转 OPEN，重置探测计数，刷新 opened_at。
            self._backend.set_state(self.name, CircuitState.OPEN)
            self._backend.set_opened_at(self.name, self._backend.now())
            self._backend.reset_half_open_calls(self.name)
            logger.warning("熔断器 %s HALF_OPEN -> OPEN（探测失败，重新熔断）", self.name)
        elif current == CircuitState.CLOSED:
            count = self._backend.incr_failure(self.name)
            if count >= self.config.failure_threshold:
                self._backend.set_state(self.name, CircuitState.OPEN)
                self._backend.set_opened_at(self.name, self._backend.now())
                logger.warning(
                    "熔断器 %s CLOSED -> OPEN（连续失败 %d 次，达阈值 %d）",
                    self.name,
                    count,
                    self.config.failure_threshold,
                )

    def reset(self) -> None:
        """强制重置为 CLOSED（管理员手动恢复或测试用）."""
        self._backend.set_state(self.name, CircuitState.CLOSED)
        self._backend.reset_failure(self.name)
        self._backend.reset_half_open_calls(self.name)
        logger.info("熔断器 %s 已重置为 CLOSED", self.name)


def get_breaker(name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    """获取或创建熔断器单例.

    Args:
        name: 熔断器唯一标识（如 ``sync:config:1``、``ingest:task:2``）。
        config: 配置，None 则用 :data:`DEFAULT_CONFIG`。仅首次创建时生效，
            已存在的 breaker 忽略新配置（避免运行时改阈值导致状态混乱）。

    Returns:
        CircuitBreaker: 与 name 绑定的单例实例。
    """
    cached = _breakers.get(name)
    if cached is not None:
        return cached
    with _breakers_lock:
        cached = _breakers.get(name)
        if cached is not None:
            return cached
        breaker = CircuitBreaker(name, config or DEFAULT_CONFIG, _resolve_backend())
        _breakers[name] = breaker
        return breaker


def list_breakers() -> list[CircuitBreaker]:
    """列出所有已注册的熔断器（按 name 排序）."""
    with _breakers_lock:
        return [_breakers[k] for k in sorted(_breakers.keys())]


__all__ = [
    "DEFAULT_CONFIG",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    "CircuitState",
    "get_breaker",
    "list_breakers",
    "reset_backend",
]
