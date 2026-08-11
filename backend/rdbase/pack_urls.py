"""fspack 打包模式 URL 路由.

复用基础 urlpatterns（admin/health/api/v1），并增加：
- /static/ 服务 Django collectstatic 收集的静态文件
- 非 API/admin/health/static 路径回退到前端 SPA（先尝试文件，再回退 index.html）

前端 SPA 构建产物位于 backend/staticfiles/spa/（由 Makefile fspack target
从 frontend/dist/ 复制）。vite 默认 base="/"，index.html 引用 /assets/xxx.js，
由 catch-all 视图从 spa/ 目录服务。
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponseBase
from django.urls import re_path
from django.views.static import serve

from .urls import urlpatterns as base_urlpatterns

_frontend_dist = Path(settings.FRONTEND_DIST)
_index_html = _frontend_dist / "index.html"


def _spa_fallback(request: HttpRequest, path: str = "") -> HttpResponseBase:
    """SPA 回退：尝试从前端目录服务文件，不存在则返回 index.html.

    - path 非空且对应文件存在 → 直接服务该文件（如 /assets/index-xxx.js）
    - 否则回退到 index.html（SPA 客户端路由由 React Router 处理）
    """
    if path:
        full_path = _frontend_dist / path
        if full_path.is_file():
            return serve(request, path, document_root=str(_frontend_dist))
    if _index_html.is_file():
        return FileResponse(_index_html.open("rb"), content_type="text/html")
    raise Http404("前端构建产物缺失：backend/staticfiles/spa/index.html")


urlpatterns = [
    # Django collectstatic 收集的静态文件（admin CSS/JS 等）
    re_path(
        r"^static/(?P<path>.*)$",
        serve,
        {"document_root": str(settings.STATIC_ROOT)},
        name="pack-static",
    ),
    # 基础路由（admin/health/api/v1/openapi）
    *base_urlpatterns,
    # SPA catch-all：非 API/admin/health/static 路径回退到前端
    re_path(
        r"^(?!api/|admin/|health/|static/)(?P<path>.*)$",
        _spa_fallback,
        name="spa-catch-all",
    ),
]
