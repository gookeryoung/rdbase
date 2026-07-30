"""manager 模块的 Pydantic Schema.

P4-1 数据浏览：行列表响应。
P4-2 数据 CRUD：行新增/更新/查询响应。
"""

from __future__ import annotations

from typing import Any

from ninja import Schema


class RowListOut(Schema):
    """行列表响应.

    ``items`` 为行数据列表（每行是 dict，键为列名）；
    ``columns`` 为实际返回的列名顺序（与 items 中 dict 的键顺序一致）。
    """

    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    columns: list[str]


class RowCreateIn(Schema):
    """行新增请求.

    ``values`` 为列名 → 值的 dict。
    """

    values: dict[str, Any]


class RowUpdateIn(Schema):
    """行更新请求.

    ``values`` 为待更新列名 → 值的 dict（不含主键列）。
    """

    values: dict[str, Any]


class RowOut(Schema):
    """单行响应."""

    row: dict[str, Any]


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


__all__ = [
    "MessageOut",
    "RowCreateIn",
    "RowListOut",
    "RowOut",
    "RowUpdateIn",
]
