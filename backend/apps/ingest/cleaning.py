"""Scrapy Item Pipeline — 数据清洗.

按 ``IngestTask.clean_config`` 配置对 spider 产出的原始 item 执行清洗，位于
FieldMappingPipeline 之前（ITEM_PIPELINES 数字更小）。空配置时透传不修改 item，
保证 P7 既有任务行为不变。

清洗器（rule.op）：

- ``on_missing``：缺失值处理。strategy=skip 丢弃整行 / fill_default 填默认值 / abort 中止
- ``cast_type``：类型转换。cast_type=int/float/bool/datetime/json
- ``normalize``：格式标准化。normalizer=trim/upper/lower/phone/email/url/date
- ``strip_html``：剥离 HTML 标签
- ``enum_map``：枚举值映射。mapping={源值: 目标值}

去重（dedup）：

- 启用时按指定字段计算 SHA-256 指纹，Redis 可用时用 SADD 判重，否则降级为内存 set
- 多 worker 部署经 Redis 共享去重状态；单 worker 内存兜底

统计通过 crawler.stats 收集：

- ``ingest_rows_cleaned``：成功清洗的行数
- ``ingest_rows_dropped``：被丢弃的行数（缺失值/类型转换失败/去重命中）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from apps.system.redis_client import get_redis

logger = logging.getLogger(__name__)

_STATS_CLEANED = "ingest_rows_cleaned"
_STATS_DROPPED = "ingest_rows_dropped"

# HTML 标签正则（剥离标签保留文本）
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# 多空白合并
_WS_RE = re.compile(r"\s+")


class DropItem(Exception):
    """清洗器判定应丢弃当前 item 时抛出.

    Scrapy 的 DropItem 异常会被 engine 捕获并计入 dropped 统计；
    本模块独立使用时不依赖 Scrapy，由 CleaningPipeline 显式捕获并统计。
    """


# ----------------------------------------------------------------
# 清洗器
# ----------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    """判断值是否为缺失（None / 空字符串 / 空白字符串）."""
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def _apply_on_missing(item: dict[str, Any], rule: dict[str, Any]) -> None:
    """缺失值处理.

    rule 字段：
    - field: 目标字段名
    - strategy: skip / fill_default / abort
    - default: fill_default 时使用的默认值
    """
    field = str(rule.get("field", ""))
    if not field:
        return
    strategy = str(rule.get("strategy", "fill_default"))
    if not _is_missing(item.get(field)):
        return

    if strategy == "skip":
        raise DropItem(f"字段 {field} 缺失，strategy=skip 丢弃")
    if strategy == "abort":
        raise DropItem(f"字段 {field} 缺失，strategy=abort 中止整批")
    # fill_default（默认）
    item[field] = rule.get("default", "")


def _apply_cast_type(item: dict[str, Any], rule: dict[str, Any]) -> None:
    """类型转换.

    rule 字段：
    - field: 目标字段名
    - cast_type: int / float / bool / datetime / json
    - datetime_format: cast_type=datetime 时的 strftime 格式（可选，默认 ISO 8601）

    转换失败时丢弃该行（DropItem）。
    """
    field = str(rule.get("field", ""))
    if not field or field not in item:
        return
    value = item[field]
    if _is_missing(value):
        return

    cast_type = str(rule.get("cast_type", "")).lower()
    try:
        if cast_type == "int":
            item[field] = int(str(value).strip())
        elif cast_type == "float":
            item[field] = float(str(value).strip())
        elif cast_type == "bool":
            item[field] = _parse_bool(value)
        elif cast_type == "datetime":
            fmt = rule.get("datetime_format")
            item[field] = _parse_datetime(value, fmt)
        elif cast_type == "json":
            item[field] = json.loads(str(value))
        # 未知 cast_type 静默跳过（容错，避免单个规则错误中断整批）
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DropItem(f"字段 {field} 类型转换 {cast_type} 失败: {exc}") from exc


def _parse_bool(value: Any) -> bool:
    """解析布尔值：支持 true/false/1/0/yes/no 等常见字面量."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ValueError(f"无法解析为布尔值: {value!r}")


def _parse_datetime(value: Any, fmt: str | None) -> str:
    """解析日期时间，返回 ISO 8601 字符串.

    Args:
        value: 输入值（字符串或 datetime）。
        fmt: 可选 strftime 格式；None 时尝试 ISO 8601 解析。
    """
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if fmt:
        return datetime.strptime(text, fmt).isoformat()
    # ISO 8601（fromisoformat 支持 'YYYY-MM-DD' 与 'YYYY-MM-DDTHH:MM:SS[+TZ]'）
    return datetime.fromisoformat(text).isoformat()


def _apply_normalize(item: dict[str, Any], rule: dict[str, Any]) -> None:
    """格式标准化.

    rule 字段：
    - field: 目标字段名
    - normalizer: trim / upper / lower / phone / email / url / date
    """
    field = str(rule.get("field", ""))
    if not field or field not in item:
        return
    value = item[field]
    if value is None or not isinstance(value, str):
        return

    normalizer = str(rule.get("normalizer", "")).lower()
    if normalizer == "trim":
        item[field] = value.strip()
    elif normalizer == "upper":
        item[field] = value.strip().upper()
    elif normalizer == "lower":
        item[field] = value.strip().lower()
    elif normalizer == "phone":
        item[field] = _normalize_phone(value)
    elif normalizer == "email":
        item[field] = value.strip().lower()
    elif normalizer == "url":
        item[field] = _normalize_url(value)
    elif normalizer == "date":
        item[field] = _normalize_date(value)
    # 未知 normalizer 静默跳过


def _normalize_phone(value: str) -> str:
    """手机号标准化：保留数字与开头的 +，去掉空格/横线/括号."""
    text = value.strip()
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    return f"+{digits}" if has_plus else digits


def _normalize_url(value: str) -> str:
    """URL 标准化：去掉末尾斜杠，scheme/host 小写."""
    text = value.strip()
    if not text:
        return text
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


def _normalize_date(value: str) -> str:
    """日期标准化：尝试常见格式，输出 YYYY-MM-DD."""
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text  # 无法解析时原样返回


def _apply_strip_html(item: dict[str, Any], rule: dict[str, Any]) -> None:
    """剥离 HTML 标签，保留文本内容并合并多余空白."""
    field = str(rule.get("field", ""))
    if not field or field not in item:
        return
    value = item[field]
    if value is None or not isinstance(value, str):
        return
    text = _HTML_TAG_RE.sub("", value)
    item[field] = _WS_RE.sub(" ", text).strip()


def _apply_enum_map(item: dict[str, Any], rule: dict[str, Any]) -> None:
    """枚举值映射：将源值转换为映射表中的目标值.

    rule 字段：
    - field: 目标字段名
    - mapping: {源值: 目标值} 字典；源值键统一转字符串比较
    - default: 未命中映射时的默认值（可选）
    """
    field = str(rule.get("field", ""))
    if not field or field not in item:
        return
    mapping = rule.get("mapping") or {}
    if not isinstance(mapping, dict):
        return
    value = item[field]
    key = str(value)
    if key in mapping:
        item[field] = mapping[key]
        return
    if "default" in rule:
        item[field] = rule["default"]


_CLEANERS: dict[str, Any] = {
    "on_missing": _apply_on_missing,
    "cast_type": _apply_cast_type,
    "normalize": _apply_normalize,
    "strip_html": _apply_strip_html,
    "enum_map": _apply_enum_map,
}


# ----------------------------------------------------------------
# 去重追踪器
# ----------------------------------------------------------------


class DedupTracker:
    """行级去重追踪器.

    按指定字段计算 SHA-256 指纹，Redis 可用时用 SADD 判重（多 worker 共享），
    否则降级为进程内 set（单进程有效）。

    Args:
        namespace: 去重命名空间（通常为任务 ID），避免不同任务互相干扰。
        fields: 参与指纹计算的字段列表；为空时用全部字段。
        ttl_hours: 指纹在 Redis 中的保留时长（小时），0 表示不过期。
    """

    def __init__(self, namespace: str, fields: list[str], ttl_hours: int = 24) -> None:
        self._namespace = namespace
        self._fields = fields
        self._ttl_seconds = max(0, int(ttl_hours) * 3600)
        self._redis = get_redis()
        self._redis_key = f"ingest:dedup:{namespace}" if self._redis is not None else ""
        # Redis 可用时不需要内存 set；不可用时降级为内存 set（多 worker 不共享）
        self._memory_set: set[str] = set()

    def is_duplicate(self, item: dict[str, Any]) -> bool:
        """判断 item 是否为重复行.

        Returns:
            True 表示重复（应丢弃），False 表示首次出现（应保留）。
        """
        fingerprint = self._fingerprint(item)
        if self._redis is not None:
            # SADD 返回 1 表示新增（非重复），0 表示已存在（重复）
            added = self._redis.sadd(self._redis_key, fingerprint)
            if added == 0:
                return True
            if self._ttl_seconds > 0:
                self._redis.expire(self._redis_key, self._ttl_seconds)
            return False
        if fingerprint in self._memory_set:
            return True
        self._memory_set.add(fingerprint)
        return False

    def _fingerprint(self, item: dict[str, Any]) -> str:
        """计算 item 的 SHA-256 指纹."""
        if self._fields:
            parts = [str(item.get(f, "")) for f in self._fields]
        else:
            parts = [f"{k}={item.get(k)}" for k in sorted(item.keys())]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------
# CleaningPipeline
# ----------------------------------------------------------------


class CleaningPipeline:
    """数据清洗 Pipeline.

    通过 :meth:`from_crawler` 创建，绑定 stats 收集器。``open_spider`` 时从 spider
    读取 clean_config 与任务 ID，初始化规则列表与去重追踪器。``process_item`` 按
    规则依次清洗，丢弃的 item 抛 :class:`DropItem` 让 Scrapy 计入 dropped 统计。

    空配置（clean_config 为空字典）时直接透传 item，不修改不统计。
    """

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []
        self._dedup_tracker: DedupTracker | None = None
        self._stats: Any = None
        self._cleaned: int = 0
        self._dropped: int = 0

    @classmethod
    def from_crawler(cls, crawler: Any) -> CleaningPipeline:  # type: ignore[missing-override-decorator, override]
        """从 crawler 创建 pipeline，绑定 stats 收集器."""
        pipeline = cls()
        pipeline._stats = crawler.stats
        return pipeline

    def open_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]
        """从 spider 读取 clean_config 并初始化规则与去重追踪器."""
        clean_config = getattr(spider, "clean_config", None)
        if not clean_config or not isinstance(clean_config, dict):
            return

        rules = clean_config.get("rules") or []
        if isinstance(rules, list):
            self._rules = [r for r in rules if isinstance(r, dict) and r.get("op") in _CLEANERS]

        dedup_cfg = clean_config.get("dedup") or {}
        if isinstance(dedup_cfg, dict) and dedup_cfg.get("enabled"):
            namespace = str(getattr(spider, "task_id", "default"))
            fields = [str(f) for f in dedup_cfg.get("fields", []) if f]
            ttl = int(dedup_cfg.get("ttl_hours", 24))
            self._dedup_tracker = DedupTracker(namespace, fields, ttl)

    def process_item(self, item: Any, spider: Any) -> Any:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """按规则清洗 item，丢弃的 item 抛 DropItem 交给 Scrapy 统计."""
        if not self._rules and self._dedup_tracker is None:
            return item

        if not isinstance(item, dict):
            return item

        cleaned = dict(item)
        for rule in self._rules:
            op = rule.get("op")
            cleaner = _CLEANERS.get(str(op))
            if cleaner is None:  # pragma: no cover - open_spider 已过滤非法 op
                continue
            try:
                cleaner(cleaned, rule)
            except DropItem:
                self._dropped += 1
                if self._stats is not None:
                    self._stats.inc_value(_STATS_DROPPED)
                raise

        if self._dedup_tracker is not None and self._dedup_tracker.is_duplicate(cleaned):
            self._dropped += 1
            if self._stats is not None:
                self._stats.inc_value(_STATS_DROPPED)
            raise DropItem("去重命中，丢弃重复行")

        self._cleaned += 1
        if self._stats is not None:
            self._stats.inc_value(_STATS_CLEANED)
        return cleaned

    def close_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """写入最终统计（兼容仅 set_value 的 stats 收集器）."""
        if self._stats is None:
            return
        # inc_value 已实时累加；此处 set_value 兜底覆盖最终值，兼容无 inc_value 的收集器
        try:
            self._stats.set_value(_STATS_CLEANED, self._cleaned)
            self._stats.set_value(_STATS_DROPPED, self._dropped)
        except Exception:  # pragma: no cover - stats 收集器异常不应中断主流程
            logger.debug("写入清洗统计失败", exc_info=True)


__all__ = [
    "CleaningPipeline",
    "DedupTracker",
    "DropItem",
]
