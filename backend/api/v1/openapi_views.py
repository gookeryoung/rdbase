"""OpenAPI spec 双视图.

req-03 item 45 与决策 #10：

- ``/api/v1/openapi.json``：管理员视图，含全部端点（与 NinjaAPI 默认
  ``/api/v1/api/openapi.json`` 等价，路径别名便于管理面文档引用）。
- ``/api/v1/datasets/openapi.json``：外部视图，仅含数据集查询/写入 +
  数据集同步触发 + 爬取任务触发端点，避免暴露管理端点信息。

设计要点：

- 复用 ``NinjaAPI.get_openapi_schema()`` 生成的 OpenAPI 文档对象，不重造。
- 外部视图通过路径白名单过滤 ``paths`` 字段，保留 ``components`` 等共享定义。
- GET 请求不进入审计中间件（仅 POST/PATCH/PUT/DELETE 写操作被审计），
  故无需在 ``_EXCLUDED_PATHS`` 中显式排除。
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import NinjaAPI

# 外部视图保留的路径白名单（仅 P9 对外端点）。
# 与 datasets_api / ingest_api 中实际注册的路径一致。
_EXTERNAL_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/datasets/{slug}/rows",
        "/api/v1/datasets/{slug}/sync",
        "/api/v1/ingest/tasks/{task_id}/trigger",
    }
)


def _schema_to_dict(schema: Any) -> dict[str, Any]:
    """将 NinjaAPI 生成的 OpenAPI schema 转为可 JSON 序列化的 dict."""
    # django-ninja 1.x 返回 pydantic BaseModel（OpenAPISchema）。
    if hasattr(schema, "model_dump"):
        return schema.model_dump(by_alias=True, exclude_none=True)
    if hasattr(schema, "dict"):
        # pydantic v1 兼容路径。
        return schema.dict(by_alias=True, exclude_none=True)
    if isinstance(schema, dict):
        return dict(schema)
    # 兜底：不应到达此分支。
    raise TypeError(f"不支持的 OpenAPI schema 类型: {type(schema)!r}")


def admin_openapi_view(api: NinjaAPI) -> Any:
    """构造管理员视图：返回完整 OpenAPI schema."""

    def _view(request: HttpRequest) -> HttpResponse:
        del request  # OpenAPI 文档无需请求上下文
        schema = _schema_to_dict(api.get_openapi_schema())
        return JsonResponse(schema)

    return _view


def external_openapi_view(api: NinjaAPI) -> Any:
    """构造外部视图：返回过滤后的 OpenAPI schema（仅对外端点）."""

    def _view(request: HttpRequest) -> HttpResponse:
        del request
        schema = _schema_to_dict(api.get_openapi_schema())
        paths = schema.get("paths") or {}
        filtered_paths: dict[str, Any] = {path: methods for path, methods in paths.items() if path in _EXTERNAL_PATHS}
        schema["paths"] = filtered_paths
        # 同步更新 info.title 标识外部视图，便于调用方区分。
        info = schema.get("info") or {}
        info["title"] = "rdbase 外部应用 API"
        schema["info"] = info
        return JsonResponse(schema)

    return _view


__all__ = [
    "admin_openapi_view",
    "external_openapi_view",
]
