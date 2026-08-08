"""根 URL 路由."""

from __future__ import annotations

from api.v1 import api as v1_api
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
    path("api/v1/", v1_api.urls),
]
