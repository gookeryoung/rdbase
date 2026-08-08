"""审计日志哈希链.

为每条 :class:`~apps.audit.models.AuditLog` 记录计算 ``prev_hash``（上一条记录的
``record_hash``）与自身 ``record_hash``（sha256(prev_hash + 规范化 JSON 负载)），
形成不可篡改的哈希链。篡改任何一条记录的字段都会导致后续校验失败，通过
:func:`verify_chain` 遍历整链即可定位被篡改的位置。

设计要点：

- 字段集合 ``_HASH_FIELDS`` 涵盖全部业务字段（用户/动作/SQL/耗时/IP 等）与
  ``created_at``（防止时间戳被改）。
- ``canonical_json`` 用 ``sort_keys=True`` 保证字段顺序稳定，``default=str``
  兜底不可序列化类型。
- ``prev_hash`` 取上一条记录（按 id 升序）的 ``record_hash``；首条记录为空字符串。
- 历史记录无 hash 时由数据迁移一次性回填（req-03 约束：历史记录 prev_hash 留空、
  hash 不校验——回填后全部纳入校验链）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import AuditLog


# 参与哈希计算的字段集合（顺序不影响哈希——JSON sort_keys 保证稳定，但显式列出便于审查）
_HASH_FIELDS: tuple[str, ...] = (
    "id",
    "user_id",
    "username",
    "action",
    "source",
    "status",
    "method",
    "path",
    "resource_type",
    "resource_id",
    "datasource_id",
    "datasource_name",
    "sql",
    "row_count",
    "elapsed_ms",
    "ip",
    "user_agent",
    "error_message",
    "extra",
    "created_at",
)


@dataclass(frozen=True)
class ChainBreak:
    """哈希链断点：某条记录的 hash 或 prev_hash 不匹配."""

    record_id: int
    expected_hash: str
    actual_hash: str
    prev_hash: str

    def to_dict(self) -> dict[str, Any]:
        """转为字典（API 响应用）."""
        return {
            "record_id": self.record_id,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "prev_hash": self.prev_hash,
        }


def _field_value(record: AuditLog, name: str) -> Any:
    """从记录中取字段值并归一化.

    - ``created_at`` 转 ISO 8601 字符串（含时区），None 时返回空串。
    - ``extra`` None 时返回空 dict。
    - ``user_id`` 直接取 FK 的 `_id` 属性（避免触发查询）。
    - 其他字段直接 getattr。
    """
    if name == "created_at":
        v = getattr(record, name, None)
        return v.isoformat() if v is not None else ""
    if name == "extra":
        v = getattr(record, name, None)
        return v if v is not None else {}
    if name == "user_id":
        return getattr(record, "user_id", None)
    return getattr(record, name, None)


def _canonical_payload(record: AuditLog) -> str:
    """构造规范化 JSON 负载（sort_keys + ensure_ascii=False + default=str）."""
    payload = {name: _field_value(record, name) for name in _HASH_FIELDS}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def compute_record_hash(record: AuditLog, prev_hash: str) -> str:
    """计算单条记录的 record_hash.

    Args:
        record: :class:`AuditLog` 实例（须已含 id 与 created_at）。
        prev_hash: 上一条记录的 record_hash，首条记录传空字符串。

    Returns:
        64 字符的 sha256 十六进制摘要。
    """
    payload = prev_hash + _canonical_payload(record)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain() -> list[ChainBreak]:
    """遍历全部审计日志（按 id 升序）校验哈希链.

    对每条记录执行两项校验：

    1. **链连续性**：当前记录的 ``prev_hash`` 应等于上一条记录的 ``record_hash``
       （首条为空字符串）。
    2. **哈希一致性**：用期望 prev_hash 重算的哈希应与存储的 ``record_hash`` 一致。

    任一不匹配即记为 :class:`ChainBreak`。篡改内容、hash、prev_hash 或删除/插入
    记录均可被检测。

    Returns:
        断点列表；空列表表示整链完整未被篡改。
    """
    from .models import AuditLog

    breaks: list[ChainBreak] = []
    expected_prev = ""
    for record in AuditLog.objects.order_by("id"):
        actual_prev = record.prev_hash or ""
        expected_hash = compute_record_hash(record, expected_prev)
        actual_hash = record.record_hash or ""
        if actual_prev != expected_prev or expected_hash != actual_hash:
            breaks.append(
                ChainBreak(
                    record_id=record.pk,  # type: ignore[no-any-return]
                    expected_hash=expected_hash,
                    actual_hash=actual_hash,
                    prev_hash=actual_prev,
                )
            )
        # 链传递：下一条的期望 prev_hash = 本条存储的 record_hash
        expected_prev = actual_hash
    return breaks


__all__ = ["ChainBreak", "compute_record_hash", "verify_chain"]
