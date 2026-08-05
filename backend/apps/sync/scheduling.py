"""同步任务定时调度工具.

封装 cron 表达式的校验与下次执行时间（next_run_at）计算，基于 croniter 库。
供 SyncService 与 API 层复用，形成「配置调度 → 计算 next_run_at → 到期执行 → 滚动更新」
的调度闭环。

Cron 表达式采用标准 5 段格式：分 时 日 月 周（如 ``*/5 * * * *`` 表示每 5 分钟）。
所有时间计算均使用 Django 时区感知的 datetime，避免 naive datetime 引发歧义。
"""

from __future__ import annotations

from datetime import datetime

from croniter import croniter
from django.utils import timezone


class CronError(ValueError):
    """Cron 表达式无效错误."""


def is_valid_cron(expression: str) -> bool:
    """校验 cron 表达式是否合法.

    Args:
        expression: 标准 5 段 cron 表达式（分 时 日 月 周）。

    Returns:
        bool: 合法返回 True，空串或非法格式返回 False。
    """
    if not expression or not expression.strip():
        return False
    return bool(croniter.is_valid(expression.strip()))


def validate_cron(expression: str) -> str:
    """校验并规范化 cron 表达式，非法则抛出 CronError.

    Args:
        expression: 标准 5 段 cron 表达式。

    Returns:
        str: 去除首尾空白后的表达式。

    Raises:
        CronError: 表达式为空或格式非法。
    """
    normalized = (expression or "").strip()
    if not is_valid_cron(normalized):
        raise CronError(f"无效的 cron 表达式: {expression!r}")
    return normalized


def compute_next_run(expression: str, *, base: datetime | None = None) -> datetime:
    """基于 cron 表达式计算下次执行时间.

    Args:
        expression: 标准 5 段 cron 表达式。
        base: 计算基准时间，None 则使用当前时区感知时间。

    Returns:
        datetime: 严格晚于 base 的下次执行时间（时区感知）。

    Raises:
        CronError: 表达式非法。
    """
    normalized = validate_cron(expression)
    base_time = base if base is not None else timezone.now()
    iterator = croniter(normalized, base_time)
    return iterator.get_next(datetime)


__all__ = ["CronError", "compute_next_run", "is_valid_cron", "validate_cron"]
