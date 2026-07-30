"""manager 模块的 Pydantic Schema.

P4-1 数据浏览：行列表响应。
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


__all__ = ["RowListOut"]
