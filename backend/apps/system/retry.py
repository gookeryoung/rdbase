"""指数退避重试工具.

为外部数据源调用提供可配置的指数退避重试，与 :mod:`apps.system.circuit_breaker` 配合：

- 熔断器在调用前判断是否放行（OPEN 时直接拒绝，不进入重试）。
- 重试器在调用失败时按指数退避等待后重试，达 ``max_retries`` 抛出最后一次异常。

退避公式：``delay = min(base_delay * (exponential_base ** attempt), max_delay) + jitter``

- ``attempt`` 从 0 开始（首次失败后等待 ``base_delay``）。
- ``jitter`` 为 ``[0, jitter_ratio * delay)`` 区间随机值，避免多客户端同步重试
  引起的「惊群」效应。

设计要点：
- 仅捕获 ``retry_on`` 指定的异常类型重试，其它异常立即抛出（避免吞掉编程错误）。
- ``sleep`` 通过参数注入，便于测试时替换为空操作。
- 装饰器与直接调用两种用法：装饰器用于无状态函数，``retry_call`` 用于需要
  动态参数（如 ``max_retries`` 来自配置）的场景。
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    """重试配置.

    Attributes:
        max_retries: 最大重试次数（不含首次调用，总尝试 = max_retries + 1）。
        base_delay: 首次重试前等待秒数（后续按指数增长）。
        max_delay: 单次等待上限（秒），避免退避无限增长。
        exponential_base: 指数底数，2 表示每次翻倍。
        jitter_ratio: 抖动比例（0-1），相对 delay 的随机增量上限。
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter_ratio: float = 0.1


def compute_backoff(attempt: int, config: RetryConfig) -> float:
    """计算第 ``attempt`` 次失败后的退避秒数（含 jitter）.

    Args:
        attempt: 已失败的尝试序号（0 表示首次失败后等待 base_delay）。
        config: 重试配置。

    Returns:
        等待秒数（已含 jitter，且不超过 max_delay）。
    """
    raw = config.base_delay * (config.exponential_base**attempt)
    capped = min(raw, config.max_delay)
    jitter = random.uniform(0, capped * config.jitter_ratio) if config.jitter_ratio > 0 else 0.0
    return capped + jitter


def retry_call(  # noqa: PLR0913
    func: Callable[P, T],
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    *,
    config: RetryConfig | None = None,
    retry_on: type[Exception] | tuple[type[Exception], ...] = Exception,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """以指数退避重试执行 ``func``.

    Args:
        func: 待执行的函数。
        args: 位置参数。
        kwargs: 关键字参数。
        config: 重试配置，None 用默认（max_retries=3, base_delay=1.0）。
        retry_on: 触发重试的异常类型；其它异常直接抛出。默认 ``Exception`` 兼容
            历史调用方，调用方应显式传入具体类型以避免吞掉编程错误。
        sleep: 等待函数（默认 ``time.sleep``），测试可注入空操作。

    Returns:
        func 的返回值。

    Raises:
        retry_on 中的异常：重试耗尽后抛出最后一次异常。
        其它异常：立即抛出不重试。
    """
    cfg = config or RetryConfig()
    call_args = args or ()
    call_kwargs = kwargs or {}
    last_exc: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return func(*call_args, **call_kwargs)
        except retry_on as exc:
            last_exc = exc
            if attempt >= cfg.max_retries:
                logger.warning(
                    "重试耗尽（attempt=%d/%d）: %s",
                    attempt + 1,
                    cfg.max_retries + 1,
                    exc,
                )
                raise
            delay = compute_backoff(attempt, cfg)
            logger.info(
                "第 %d/%d 次重试，等待 %.2fs: %s",
                attempt + 1,
                cfg.max_retries,
                delay,
                exc,
            )
            sleep(delay)

    # 不可达：循环要么 return，要么 raise。保险起见抛出最后一次异常。
    if last_exc is not None:  # pragma: no cover - 防御性分支
        raise last_exc
    raise RuntimeError("retry_call 未执行任何尝试")  # pragma: no cover


def with_retry(
    *,
    config: RetryConfig | None = None,
    retry_on: type[Exception] | tuple[type[Exception], ...] = Exception,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """装饰器工厂：为函数包裹指数退避重试.

    Args:
        config: 重试配置。
        retry_on: 触发重试的异常类型。
        sleep: 等待函数（测试可注入）。

    Returns:
        装饰器函数。

    用法::

        @with_retry(config=RetryConfig(max_retries=2), retry_on=ConnectionError)
        def fetch(url: str) -> bytes: ...
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return retry_call(
                func,
                args=args,
                kwargs=kwargs,
                config=config,
                retry_on=retry_on,
                sleep=sleep,
            )

        return wrapper

    return decorator


__all__ = [
    "RetryConfig",
    "compute_backoff",
    "retry_call",
    "with_retry",
]
