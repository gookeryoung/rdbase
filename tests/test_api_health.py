"""健康检查与 API 入口测试."""

from __future__ import annotations

import json

from django.http import HttpResponse
from django.test import Client


def test_health_check_returns_ok() -> None:
    """GET /health/ 应返回 200 与 ok 状态."""
    client = Client()
    response = client.get("/health/")
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
