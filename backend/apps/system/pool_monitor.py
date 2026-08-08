"""SQLAlchemy 引擎连接池监控.

遍历 ``apps.datasources.engine._engine_cache``，解析各引擎 ``pool.status()``
字符串，给出连接占用率与泄露告警。供健康检查与管理员 API 调用。
"""

from __future__ import annotations

import logging
import operator
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.utils import DatabaseError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# SQLAlchemy QueuePool.status() 文本样例：
#   "Pool size: 5  Connections in pool: 2  Current Overflow: 0  Current Checked out connections: 1"
# 各字段用命名捕获组提取。
_POOL_PATTERN = re.compile(
    r"Pool size:\s*(?P<size>\d+)"
    r"\s+Connections in pool:\s*(?P<in>\d+)"
    r"\s+Current Overflow:\s*(?P<overflow>-?\d+)"
    r"\s+Current Checked out connections:\s*(?P<out>\d+)",
)

# 连接占用率（checked_out / pool_size）超过此阈值标记泄露告警
_LEAK_RATIO_THRESHOLD = 0.8


@dataclass(frozen=True)
class PoolStat:
    """单个数据源引擎池的状态快照."""

    datasource_id: int
    datasource_name: str | None
    status_text: str
    pool_size: int | None
    checked_in: int | None
    checked_out: int | None
    overflow: int | None
    leak_alert: bool
    leak_detail: str


def _parse_status(status_text: str) -> dict[str, int]:
    """解析 ``pool.status()`` 文本，返回各计数字段.

    Returns:
        字典 keys: size, checked_in, checked_out, overflow；解析失败返回空字典。
    """
    match = _POOL_PATTERN.search(status_text)
    if match is None:
        return {}
    return {
        "size": int(match.group("size")),
        "checked_in": int(match.group("in")),
        "checked_out": int(match.group("out")),
        "overflow": int(match.group("overflow")),
    }


def _detect_leak(parsed: dict[str, int]) -> tuple[bool, str]:
    """按占用率判断是否标记泄露告警.

    Returns:
        元组 (是否告警, 详情)。
    """
    size = parsed.get("size")
    checked_out = parsed.get("checked_out")
    if size is None or checked_out is None or size <= 0:
        return False, ""
    ratio = checked_out / size
    if ratio > _LEAK_RATIO_THRESHOLD:
        return True, f"连接占用率 {ratio:.0%} 超过阈值 {_LEAK_RATIO_THRESHOLD:.0%}，疑似泄露"
    return False, ""


def _fetch_datasource_names(ds_ids: list[int]) -> dict[int, str]:
    """批量查询数据源名称，避免 N+1.

    Args:
        ds_ids: 数据源主键列表。

    Returns:
        主键 -> 名称 的字典；查询异常时返回空字典（不影响监控主流程）。
    """
    if not ds_ids:
        return {}
    from apps.datasources.models import DataSource

    try:
        qs = DataSource.objects.filter(pk__in=ds_ids).values_list("pk", "name")
        return dict(qs)
    except DatabaseError:
        logger.exception("查询数据源名称失败，连接池名称将以匿名呈现")
        return {}


def collect_pool_stats() -> list[PoolStat]:
    """采集所有已缓存引擎池的状态快照.

    Returns:
        ``PoolStat`` 列表，按数据源主键升序。
    """
    # 延迟导入避免与 engine 模块形成加载期循环
    from apps.datasources.engine import _engine_cache, _engine_cache_lock

    with _engine_cache_lock:
        items = list(_engine_cache.items())
    if not items:
        return []
    name_map = _fetch_datasource_names([pk for pk, _ in items])
    stats: list[PoolStat] = []
    for ds_id, engine in items:
        stat = _build_stat(ds_id, engine, name_map.get(ds_id))
        stats.append(stat)
    stats.sort(key=operator.attrgetter("datasource_id"))
    return stats


def _build_stat(ds_id: int, engine: Engine, name: str | None) -> PoolStat:
    """构造单个引擎池状态快照."""
    try:
        status_text = engine.pool.status()
    except (AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("读取引擎 %s 的 pool.status() 失败: %s", ds_id, exc)
        return PoolStat(
            datasource_id=ds_id,
            datasource_name=name,
            status_text="",
            pool_size=None,
            checked_in=None,
            checked_out=None,
            overflow=None,
            leak_alert=False,
            leak_detail=f"读取池状态失败: {exc}",
        )
    parsed = _parse_status(status_text)
    leak_alert, leak_detail = _detect_leak(parsed)
    return PoolStat(
        datasource_id=ds_id,
        datasource_name=name,
        status_text=status_text,
        pool_size=parsed.get("size"),
        checked_in=parsed.get("checked_in"),
        checked_out=parsed.get("checked_out"),
        overflow=parsed.get("overflow"),
        leak_alert=leak_alert,
        leak_detail=leak_detail,
    )


__all__ = [
    "PoolStat",
    "collect_pool_stats",
]
