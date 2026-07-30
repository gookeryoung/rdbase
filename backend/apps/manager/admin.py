"""manager admin 注册.

manager 应用不持有 Django 模型（数据浏览直接通过 SQLAlchemy 反射目标库），
因此 admin 模块为空占位，保留以便后续扩展（如审计日志模型）。
"""

from __future__ import annotations
