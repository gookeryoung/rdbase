"""API v1 路由聚合.

各业务模块的 Router 在对应阶段实现后挂载到此处。
"""

from __future__ import annotations

from apps.accounts.api import router as accounts_router
from apps.accounts.users import router as users_router
from apps.audit.api import router as audit_router
from apps.datasources.api import router as datasources_router
from apps.designer.api import router as designer_router
from apps.manager.api import router as manager_router
from apps.settings.api import router as settings_router
from apps.sync.api import router as sync_router
from ninja import NinjaAPI

api = NinjaAPI(
    title="rdbase API",
    version="1.0.0",
    description="通用数据库管理平台 API",
)

api.add_router("/auth", accounts_router)
api.add_router("/users", users_router)
api.add_router("/datasources", datasources_router)
api.add_router("/designer", designer_router)
api.add_router("/manager", manager_router)
api.add_router("/audit", audit_router)
api.add_router("/settings", settings_router)
api.add_router("/sync", sync_router)
