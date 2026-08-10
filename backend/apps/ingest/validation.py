"""Scrapy Item Pipeline — 数据质量校验.

按 ``IngestTask.validation_config`` 配置对清洗后的 item 执行质量校验，位于
CleaningPipeline(200) 与 FieldMappingPipeline(300) 之间（ITEM_PIPELINES 数字 250）。
空配置时透传不修改 item，保证 P7/P8-Q1 既有任务行为不变。

校验规则（rule.op）：

- ``required``：必填校验。空值视为失败。
- ``range``：数值范围。min/max 任一可选；非数值或越界视为失败。
- ``regex``：正则匹配。pattern 必填；不匹配视为失败。
- ``enum``：枚举值。values 列表必填；不在列表内视为失败。
- ``unique``：批次内唯一性。按 field 值在本次执行内去重；重复视为失败。
- ``expression``：自定义表达式。expr 必填，形如 ``"value > 0"``；
  以 ``value`` 与 ``item`` 为变量名安全求值（限定 ``__builtins__``），
  求值异常或结果非真视为失败。

校验失败不丢弃 item（与 CleaningPipeline 的 DropItem 不同），仅记录失败样本并
累加 stats；item 继续流向 FieldMappingPipeline 写入目标表。失败样本最多保留
``MAX_SAMPLES_PER_RULE``（默认 20）条，避免大流量场景下膨胀。

统计通过 crawler.stats 收集：

- ``ingest_validation_total``：执行的规则次数总数
- ``ingest_validation_passed``：通过次数
- ``ingest_validation_failed``：失败次数
- ``ingest_rows_invalid``：至少有一条规则失败的 item 数（去重计数）

close_spider 时按 (field, rule) 聚合统计写入 :class:`IngestQualityReport`，
关联到任务最近一次 IngestLog。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_STATS_TOTAL = "ingest_validation_total"
_STATS_PASSED = "ingest_validation_passed"
_STATS_FAILED = "ingest_validation_failed"
_STATS_INVALID_ROWS = "ingest_rows_invalid"

# 每条规则最多保留的失败样本数
MAX_SAMPLES_PER_RULE = 20


# ----------------------------------------------------------------
# 校验器
# ----------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    """判断值是否为缺失（None / 空字符串 / 空白字符串 / 空集合）."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _validate_required(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    """必填校验：字段非缺失即通过.

    rule 字段：``field``。
    """
    field = str(rule.get("field", ""))
    if not field:
        return True
    return not _is_missing(item.get(field))


def _validate_range(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    """数值范围校验：min <= value <= max.

    rule 字段：``field``、``min``（可选）、``max``（可选）。
    非数值或越界视为失败；缺失值视为通过（应由 required 规则负责）。
    """
    field = str(rule.get("field", ""))
    if not field:
        return True
    value = item.get(field)
    if _is_missing(value):
        return True
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    if "min" in rule and num < float(rule["min"]):
        return False
    return not ("max" in rule and num > float(rule["max"]))


def _validate_regex(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    """正则匹配校验：value 匹配 pattern 即通过.

    rule 字段：``field``、``pattern``。
    缺失值视为通过（应由 required 规则负责）；非字符串先转字符串再匹配。
    """
    field = str(rule.get("field", ""))
    if not field:
        return True
    value = item.get(field)
    if _is_missing(value):
        return True
    pattern = rule.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return True
    try:
        return re.search(pattern, str(value)) is not None
    except re.error:
        return False


def _validate_enum(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    """枚举值校验：value 在 values 列表内即通过.

    rule 字段：``field``、``values``（list）。
    缺失值视为通过；values 非列表或空时视为通过（容错）。
    """
    field = str(rule.get("field", ""))
    if not field:
        return True
    value = item.get(field)
    if _is_missing(value):
        return True
    values = rule.get("values")
    if not isinstance(values, list) or not values:
        return True
    return value in values or str(value) in [str(v) for v in values]


def _validate_unique(item: dict[str, Any], rule: dict[str, Any], seen: set[Any]) -> bool:
    """批次内唯一性校验：value 未在本次执行中出现过即通过.

    rule 字段：``field``。
    缺失值视为通过；首次出现通过，重复出现失败。
    seen 由调用方维护，跨 item 累积。
    """
    field = str(rule.get("field", ""))
    if not field:
        return True
    value = item.get(field)
    if _is_missing(value):
        return True
    # 用 frozenset 包装 dict/list 以便加入 set
    key = _hashable_key(value)
    if key in seen:
        return False
    seen.add(key)
    return True


def _hashable_key(value: Any) -> Any:
    """将任意值转为可哈希的键（dict/list 转为元组）."""
    if isinstance(value, dict):
        return ("__dict__", tuple(sorted((k, _hashable_key(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("__list__", tuple(_hashable_key(v) for v in value))
    return value


def _validate_expression(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    """自定义表达式校验：以 ``value`` 与 ``item`` 为变量安全求值.

    rule 字段：``field``（可选，用于取 value）、``expr``。
    缺失值视为通过；求值异常或结果非真视为失败。

    安全策略：``__builtins__`` 清空，仅暴露 ``abs``/``len``/``min``/``max``/``round``
    与 ``str``/``int``/``float``/``bool`` 等常用函数，禁用 import 与属性访问危险函数。
    """
    expr = rule.get("expr")
    if not isinstance(expr, str) or not expr.strip():
        return True
    field = str(rule.get("field", ""))
    value = item.get(field) if field else None
    if _is_missing(value) and field:
        return True
    safe_builtins = {
        "abs": abs,
        "len": len,
        "min": min,
        "max": max,
        "round": round,
        "sum": sum,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "True": True,
        "False": False,
        "None": None,
    }
    try:
        result = eval(expr, {"__builtins__": safe_builtins}, {"value": value, "item": item})
    except Exception:
        return False
    return bool(result)


_VALIDATORS: dict[str, Any] = {
    "required": _validate_required,
    "range": _validate_range,
    "regex": _validate_regex,
    "enum": _validate_enum,
    "unique": _validate_unique,
    "expression": _validate_expression,
}


# ----------------------------------------------------------------
# 规则统计累加器
# ----------------------------------------------------------------


class _RuleStats:
    """单条规则的累计统计.

    记录该规则在本次执行中的通过/失败次数与失败样本。
    """

    def __init__(self, field: str, op: str) -> None:
        self.field = field
        self.op = op
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.samples: list[dict[str, Any]] = []

    def record(self, ok: bool, value: Any, reason: str = "") -> None:
        """记录一次校验结果."""
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            if len(self.samples) < MAX_SAMPLES_PER_RULE:
                self.samples.append({"value": _coerce_sample(value), "reason": reason})

    @property
    def pass_rate(self) -> float:
        """通过率（0-100，保留一位小数）."""
        if self.total == 0:
            return 100.0
        return round(self.passed / self.total * 100, 1)


def _coerce_sample(value: Any) -> Any:
    """将样本值转为 JSON 可序列化形式（dict/list 保持原样，其他转字符串）."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        # 体积过大时截断
        try:
            import json

            text = json.dumps(value, ensure_ascii=False, default=str)
            return text[:500]
        except (TypeError, ValueError):
            return str(value)[:500]
    return str(value)[:500]


# ----------------------------------------------------------------
# ValidationPipeline
# ----------------------------------------------------------------


class ValidationPipeline:
    """数据质量校验 Pipeline.

    通过 :meth:`from_crawler` 创建，绑定 stats 收集器。``open_spider`` 时从 spider
    读取 validation_config 与任务 ID，初始化规则列表与每条规则的统计累加器；
    ``process_item`` 按规则依次校验，失败记录样本但**不丢弃 item**；
    ``close_spider`` 时按 (field, rule) 聚合写入 :class:`IngestQualityReport`，
    关联到任务最近一次 IngestLog。

    空配置（validation_config 为空字典）时直接透传 item，不校验不统计。
    """

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []
        self._stats: Any = None
        self._task_id: int | None = None
        self._rule_stats: dict[tuple[str, str], _RuleStats] = {}
        # unique 规则按 field 维护 seen set
        self._unique_seen: dict[str, set[Any]] = {}
        self._invalid_rows: int = 0
        self._total_checks: int = 0
        self._passed_checks: int = 0
        self._failed_checks: int = 0

    @classmethod
    def from_crawler(cls, crawler: Any) -> ValidationPipeline:  # type: ignore[missing-override-decorator, override]
        """从 crawler 创建 pipeline，绑定 stats 收集器."""
        pipeline = cls()
        pipeline._stats = crawler.stats
        return pipeline

    def open_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]
        """从 spider 读取 validation_config 并初始化规则列表."""
        validation_config = getattr(spider, "validation_config", None)
        if not validation_config or not isinstance(validation_config, dict):
            return
        rules = validation_config.get("rules") or []
        if isinstance(rules, list):
            self._rules = [r for r in rules if isinstance(r, dict) and r.get("op") in _VALIDATORS]
        self._task_id = getattr(spider, "task_id", None)
        # 初始化每条规则的统计累加器与 unique seen set
        for rule in self._rules:
            field = str(rule.get("field", ""))
            op = str(rule.get("op", ""))
            self._rule_stats[(field, op)] = _RuleStats(field, op)
            if op == "unique":
                self._unique_seen.setdefault(field, set())

    def _evaluate_rule(self, rule: dict[str, Any], item: dict[str, Any]) -> tuple[str, str, bool, Any]:
        """执行单条规则，返回 (field, op, ok, value).

        校验器异常视为失败，不中断主流程。
        """
        field = str(rule.get("field", ""))
        op = str(rule.get("op", ""))
        validator = _VALIDATORS.get(op)
        if validator is None:  # pragma: no cover - open_spider 已过滤
            return field, op, True, None
        value = item.get(field)
        try:
            if op == "unique":
                seen = self._unique_seen.get(field)
                if seen is None:  # pragma: no cover - open_spider 已初始化
                    seen = set()
                    self._unique_seen[field] = seen
                ok = validator(item, rule, seen)
            else:
                ok = validator(item, rule)
        except Exception:  # 校验器异常不应中断主流程
            ok = False
        return field, op, ok, value

    def process_item(self, item: Any, spider: Any) -> Any:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """按规则校验 item，失败记录样本但不丢弃.

        空配置或非 dict item 直接透传。
        """
        if not self._rules:
            return item
        if not isinstance(item, dict):
            return item

        row_has_failure = False
        for rule in self._rules:
            field, op, ok, value = self._evaluate_rule(rule, item)
            stats = self._rule_stats.get((field, op))
            if stats is not None:
                reason = "" if ok else f"{op} 校验失败"
                stats.record(ok, value, reason)
            self._total_checks += 1
            if ok:
                self._passed_checks += 1
                if self._stats is not None:
                    self._stats.inc_value(_STATS_PASSED)
            else:
                self._failed_checks += 1
                row_has_failure = True
                if self._stats is not None:
                    self._stats.inc_value(_STATS_FAILED)
            if self._stats is not None:
                self._stats.inc_value(_STATS_TOTAL)

        if row_has_failure:
            self._invalid_rows += 1
            if self._stats is not None:
                self._stats.inc_value(_STATS_INVALID_ROWS)
        return item

    def close_spider(self, spider: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """聚合规则统计写入 IngestQualityReport.

        - 写入 stats 总计。
        - 按规则批量创建 IngestQualityReport，关联到任务最近一次 IngestLog。
        - 无 task_id 或无 log 时仅写 stats，不创建报告。
        """
        if self._stats is not None:
            try:
                self._stats.set_value(_STATS_TOTAL, self._total_checks)
                self._stats.set_value(_STATS_PASSED, self._passed_checks)
                self._stats.set_value(_STATS_FAILED, self._failed_checks)
                self._stats.set_value(_STATS_INVALID_ROWS, self._invalid_rows)
            except Exception:  # pragma: no cover - stats 收集器异常不应中断主流程
                logger.debug("写入校验统计失败", exc_info=True)

        if not self._rule_stats:
            return
        if self._task_id is None:
            return

        # 延迟导入避免循环依赖（models 在 apps.ready 时可能未就绪）
        from apps.ingest.models import IngestLog, IngestQualityReport

        log = IngestLog.objects.filter(task_id=self._task_id).order_by("-started_at").first()
        if log is None:
            logger.warning("校验报告未关联日志：task_id=%s 无 IngestLog 记录", self._task_id)
            return

        objs = [
            IngestQualityReport(
                task_id=self._task_id,
                log=log,
                field=stats.field,
                rule=stats.op,
                total_count=stats.total,
                passed_count=stats.passed,
                failed_count=stats.failed,
                pass_rate=stats.pass_rate,
                failure_samples=stats.samples,
            )
            for stats in self._rule_stats.values()
            if stats.total > 0
        ]
        if objs:
            IngestQualityReport.objects.bulk_create(objs)


__all__ = [
    "MAX_SAMPLES_PER_RULE",
    "ValidationPipeline",
]
