"""根 URL 路由."""

from __future__ import annotations

from api.v1 import api as v1_api
from api.v1.openapi_views import admin_openapi_view, external_openapi_view
from apps.system.health import live_view, ready_view
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
    # 存活探针：轻量响应，供负载均衡探活
    path("health/live", live_view, name="health-live"),
    # 就绪探针：跑全部检查器，任一 unhealthy 返回 503
    path("health/ready", ready_view, name="health-ready"),
    # 兼容旧路径：返回与 /health/ready 相同的聚合结果
    path("health/", ready_view, name="health-check"),
    # OpenAPI spec 双视图（req-03 item 45 决策 #10）：
    # 必须在 ``api/v1/`` include 之前注册，否则会被 datasets router 的
    # ``GET /{slug}`` 模式以 slug="openapi.json" 匹配并要求 JWT 鉴权。
    # - /api/v1/openapi.json：管理员视图，含全部端点
    # - /api/v1/datasets/openapi.json：外部视图，仅数据集 + 触发端点
    path("api/v1/openapi.json", admin_openapi_view(v1_api), name="openapi-admin"),
    path(
        "api/v1/datasets/openapi.json",
        external_openapi_view(v1_api),
        name="openapi-external",
    ),
    path("api/v1/", v1_api.urls),
]
