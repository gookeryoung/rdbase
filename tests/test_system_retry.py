"""指数退避重试工具测试.

覆盖：退避公式、retry_call 重试与耗尽、装饰器用法、异常类型过滤、sleep 注入。
"""

from __future__ import annotations

import pytest
from apps.system.retry import RetryConfig, compute_backoff, retry_call, with_retry


class TestComputeBackoff:
    """退避公式计算."""

    def test_base_delay_on_first_attempt(self) -> None:
        """首次失败（attempt=0）等待 base_delay."""
        cfg = RetryConfig(max_retries=3, base_delay=1.0, jitter_ratio=0.0)
        assert compute_backoff(0, cfg) == 1.0

    def test_exponential_growth(self) -> None:
        """退避按指数底数增长（无 jitter）."""
        cfg = RetryConfig(max_retries=5, base_delay=1.0, exponential_base=2.0, jitter_ratio=0.0)
        assert compute_backoff(0, cfg) == 1.0
        assert compute_backoff(1, cfg) == 2.0
        assert compute_backoff(2, cfg) == 4.0
        assert compute_backoff(3, cfg) == 8.0

    def test_capped_by_max_delay(self) -> None:
        """退避不超过 max_delay."""
        cfg = RetryConfig(max_retries=5, base_delay=10.0, exponential_base=2.0, max_delay=15.0, jitter_ratio=0.0)
        assert compute_backoff(0, cfg) == 10.0
        assert compute_backoff(1, cfg) == 15.0  # 20 被截断
        assert compute_backoff(5, cfg) == 15.0

    def test_jitter_adds_randomness(self) -> None:
        """jitter 使退避在 [base, base*(1+jitter_ratio)) 区间."""
        cfg = RetryConfig(base_delay=10.0, jitter_ratio=0.1)
        for _ in range(20):
            delay = compute_backoff(0, cfg)
            assert 10.0 <= delay < 11.0

    def test_zero_jitter_no_randomness(self) -> None:
        """jitter_ratio=0 时退避确定."""
        cfg = RetryConfig(base_delay=2.0, jitter_ratio=0.0)
        assert compute_backoff(0, cfg) == 2.0


class TestRetryCall:
    """retry_call 直接调用."""

    def test_success_on_first_attempt(self) -> None:
        """首次成功不重试."""

        def func() -> str:
            return "ok"

        result = retry_call(func, config=RetryConfig(max_retries=3))
        assert result == "ok"

    def test_retries_on_specified_exception(self) -> None:
        """指定异常类型触发重试，最终成功."""
        calls = {"n": 0}

        def func() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return "ok"

        sleeps: list[float] = []
        result = retry_call(
            func,
            config=RetryConfig(max_retries=3, jitter_ratio=0.0),
            retry_on=ValueError,
            sleep=sleeps.append,
        )
        assert result == "ok"
        assert calls["n"] == 3
        assert len(sleeps) == 2

    def test_raises_after_max_retries(self) -> None:
        """重试耗尽抛出最后一次异常."""
        calls = {"n": 0}

        def func() -> str:
            calls["n"] += 1
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            retry_call(
                func,
                config=RetryConfig(max_retries=2, jitter_ratio=0.0),
                retry_on=ValueError,
                sleep=lambda _d: None,
            )
        assert calls["n"] == 3  # 1 首次 + 2 重试

    def test_non_matching_exception_not_retried(self) -> None:
        """非 retry_on 异常立即抛出不重试."""
        calls = {"n": 0}

        def func() -> str:
            calls["n"] += 1
            raise TypeError("not retried")

        with pytest.raises(TypeError):
            retry_call(
                func,
                config=RetryConfig(max_retries=3),
                retry_on=ValueError,
                sleep=lambda _d: None,
            )
        assert calls["n"] == 1

    def test_passes_args_and_kwargs(self) -> None:
        """位置与关键字参数透传."""

        def func(a: int, b: int = 0) -> int:
            return a + b

        result = retry_call(func, args=(1,), kwargs={"b": 2}, config=RetryConfig(max_retries=1))
        assert result == 3

    def test_tuple_of_exception_types(self) -> None:
        """retry_on 支持异常元组."""
        calls = {"n": 0}

        def func() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("v")
            if calls["n"] == 2:
                raise ConnectionError("c")
            return "ok"

        result = retry_call(
            func,
            config=RetryConfig(max_retries=3, jitter_ratio=0.0),
            retry_on=(ValueError, ConnectionError),
            sleep=lambda _d: None,
        )
        assert result == "ok"
        assert calls["n"] == 3


class TestWithRetryDecorator:
    """with_retry 装饰器."""

    def test_decorator_success(self) -> None:
        """装饰器包裹成功函数."""

        @with_retry(config=RetryConfig(max_retries=2), retry_on=ValueError)
        def func(x: int) -> int:
            return x * 2

        assert func(5) == 10

    def test_decorator_retries(self) -> None:
        """装饰器触发重试."""

        calls = {"n": 0}

        @with_retry(
            config=RetryConfig(max_retries=2, jitter_ratio=0.0),
            retry_on=ConnectionError,
            sleep=lambda _d: None,
        )
        def func() -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("down")
            return "recovered"

        assert func() == "recovered"
        assert calls["n"] == 2

    def test_decorator_preserves_metadata(self) -> None:
        """装饰器保留原函数名与文档."""

        @with_retry(retry_on=ValueError, sleep=lambda _d: None)
        def my_func() -> None:
            """My docstring."""

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "My docstring."


class TestRetryConfigDefaults:
    """RetryConfig 默认值."""

    def test_default_values(self) -> None:
        """默认配置符合预期."""
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 30.0
        assert cfg.exponential_base == 2.0
        assert cfg.jitter_ratio == 0.1

    def test_config_is_frozen(self) -> None:
        """RetryConfig 不可变."""
        from dataclasses import FrozenInstanceError

        cfg = RetryConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.max_retries = 99  # type: ignore[misc]
