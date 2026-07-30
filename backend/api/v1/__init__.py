"""API v1 路由聚合.

各业务模块的 Router 在对应阶段实现后挂载到此处。
"""

from __future__ import annotations

from apps.accounts.api import router as accounts_router
from apps.accounts.users import router as users_router
from ninja import NinjaAPI

api = NinjaAPI(
    title="rdbase API",
    version="1.0.0",
    description="通用数据库管理平台 API",
)

api.add_router("/auth", accounts_router)
api.add_router("/users", users_router)
