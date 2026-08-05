"""同步调度工具（cron 解析）测试.

覆盖 scheduling 模块的 cron 校验、规范化与 next_run_at 计算，
含边界与非法输入路径。
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from apps.sync.scheduling import (
    CronError,
    compute_next_run,
    is_valid_cron,
    validate_cron,
)


class TestIsValidCron:
    """is_valid_cron 校验测试."""

    @pytest.mark.parametrize(
        "expression",
        [
            "*/5 * * * *",
            "0 0 * * *",
            "30 8 * * 1-5",
            "0 */2 * * *",
            "15,45 * * * *",
        ],
    )
    def test_valid_expressions(self, expression: str) -> None:
        """合法 cron 表达式应返回 True."""
        assert is_valid_cron(expression) is True

    @pytest.mark.parametrize(
        "expression",
        [
            "",
            "   ",
            "bad cron",
            "60 * * * *",
            "* * *",
            "not-a-cron",
        ],
    )
    def test_invalid_expressions(self, expression: str) -> None:
        """非法或空 cron 表达式应返回 False."""
        assert is_valid_cron(expression) is False

    def test_whitespace_is_trimmed(self) -> None:
        """首尾空白应被忽略后再校验."""
        assert is_valid_cron("  */5 * * * *  ") is True


class TestValidateCron:
    """validate_cron 规范化与异常测试."""

    def test_returns_normalized_expression(self) -> None:
        """合法表达式应返回去除首尾空白的结果."""
        assert validate_cron("  0 0 * * *  ") == "0 0 * * *"

    def test_invalid_raises_cron_error(self) -> None:
        """非法表达式应抛出 CronError."""
        with pytest.raises(CronError, match="无效的 cron 表达式"):
            validate_cron("bad cron")

    def test_empty_raises_cron_error(self) -> None:
        """空表达式应抛出 CronError."""
        with pytest.raises(CronError, match="无效的 cron 表达式"):
            validate_cron("")


class TestComputeNextRun:
    """compute_next_run 计算测试."""

    def test_next_run_every_5_minutes(self) -> None:
        """每 5 分钟表达式应计算出下一个 5 分钟边界."""
        base = datetime(2026, 8, 5, 10, 2, tzinfo=dt_timezone.utc)
        nxt = compute_next_run("*/5 * * * *", base=base)
        assert nxt == datetime(2026, 8, 5, 10, 5, tzinfo=dt_timezone.utc)

    def test_next_run_daily_midnight(self) -> None:
        """每日 0 点表达式应计算出次日零点."""
        base = datetime(2026, 8, 5, 10, 0, tzinfo=dt_timezone.utc)
        nxt = compute_next_run("0 0 * * *", base=base)
        assert nxt == datetime(2026, 8, 6, 0, 0, tzinfo=dt_timezone.utc)

    def test_next_run_strictly_after_base(self) -> None:
        """即使 base 恰好落在触发点，也应返回严格晚于 base 的时间."""
        base = datetime(2026, 8, 5, 10, 5, tzinfo=dt_timezone.utc)
        nxt = compute_next_run("*/5 * * * *", base=base)
        assert nxt > base
        assert nxt == datetime(2026, 8, 5, 10, 10, tzinfo=dt_timezone.utc)

    def test_next_run_is_timezone_aware(self) -> None:
        """返回的时间应为时区感知 datetime."""
        base = datetime(2026, 8, 5, 10, 0, tzinfo=dt_timezone.utc)
        nxt = compute_next_run("*/5 * * * *", base=base)
        assert nxt.tzinfo is not None

    def test_next_run_uses_now_when_base_none(self, db: object) -> None:
        """base 为 None 时应基于当前时间计算未来时间."""
        from django.utils import timezone

        before = timezone.now()
        nxt = compute_next_run("* * * * *")
        assert nxt > before

    def test_invalid_expression_raises(self) -> None:
        """非法表达式应抛出 CronError."""
        with pytest.raises(CronError):
            compute_next_run("bad cron")
