"""根 URL 路由."""

from __future__ import annotations

from api.v1 import api as v1_api
from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import path


def health_check(_request: HttpRequest) -> JsonResponse:
    """健康检查端点，供负载均衡与前端探活使用."""
    return JsonResponse({"status": "ok", "project": "rdbase"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health-check"),
    path("api/v1/", v1_api.urls),
]
