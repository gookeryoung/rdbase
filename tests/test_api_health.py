"""健康检查与 API 入口测试."""

from __future__ import annotations

import json

from django.http import HttpResponse
from django.test import Client


def test_health_live_returns_ok() -> None:
    """GET /health/live 应返回 200 与 ok 状态（轻量探针，不查 DB）."""
    client = Client()
    response = client.get("/health/live")
    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == "ok"
    assert body["project"] == "rdbase"


def test_api_v1_openapi_json_available() -> None:
    """GET /api/v1/openapi.json 应返回 OpenAPI schema."""
    client = Client()
    response = client.get("/api/v1/openapi.json")
    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    schema = json.loads(response.content)
    assert schema["info"]["title"] == "rdbase API"
    assert schema["info"]["version"] == "1.0.0"
    # auth 与 users 两组路由均应出现在 paths 中
    assert "/api/v1/auth/login" in schema["paths"]
    assert "/api/v1/users" in schema["paths"]


def test_api_v1_swagger_docs_available() -> None:
    """GET /api/v1/docs 应返回 Swagger UI HTML 页面."""
    client = Client()
    response = client.get("/api/v1/docs")
    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    # Swagger UI 页面应包含标题或 swagger-ui 关键字
    content = response.content.decode("utf-8")
    assert "swagger" in content.lower() or "rdbase API" in content
