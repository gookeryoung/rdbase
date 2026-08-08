---
name: "python-fastapi"
description: "FastAPI Web 服务开发技能：路由、Pydantic 模型、依赖注入、中间件、后台任务、异步处理、异常处理、TestClient 测试等代码模板。当需要创建或修改 FastAPI 应用、定义 API 路由、设计请求/响应模型、实现依赖注入、编写异步处理器或测试 Web 接口时调用。"
---

# FastAPI Web 服务开发

自包含的 FastAPI 指南：路由、模型、依赖注入、中间件、异步、测试。基于 FastAPI + uvicorn + Pydantic v2。所有代码遵循 `python-standards` SKILL（类型注解、中文 docstring）。

## 何时调用

- 创建或修改 FastAPI 应用、定义 API 路由
- 设计请求体/响应模型（Pydantic）
- 实现依赖注入（DB 会话、认证、配置）
- 编写中间件（CORS、日志、限流）
- 实现后台任务、异步处理器
- 处理异常与错误响应
- 编写 API 测试（TestClient）

## 应用结构与入口

```
src/rdbase/
├── app.py              # FastAPI 实例 + 路由注册 + 启动
├── config.py           # 配置（环境变量加载）
├── models/
│   ├── __init__.py
│   ├── user.py         # Pydantic 请求/响应模型
│   └── common.py       # 通用响应包装
├── routers/
│   ├── __init__.py
│   ├── users.py        # 用户相关路由
│   └── items.py        # 资源相关路由
├── deps.py             # 依赖注入函数
├── services/           # 业务逻辑（纯 Python，不依赖 FastAPI）
│   └── user_service.py
└── main.py             # uvicorn 启动入口
```

入口与应用实例：

```python
"""rdbase Web 应用（FastAPI）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI

from rdbase.routers import items, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时清理。"""
    # 启动：初始化数据库连接池、缓存等
    yield
    # 关闭：清理资源


app = FastAPI(
    title="rdbase",
    description="科研数据库。",
    lifespan=lifespan,
)

# 注册路由
app.include_router(users.router, prefix="/users", tags=["用户"])
app.include_router(items.router, prefix="/items", tags=["资源"])


@app.get("/health")
def health_check() -> dict[str, str]:
    """健康检查端点。"""
    return {"status": "ok", "project": "rdbase"}
```

启动（`main.py`）：

```python
"""uvicorn 启动入口。"""

from __future__ import annotations


def main() -> None:  # pragma: no cover
    """启动 uvicorn 服务器。"""
    import uvicorn

    uvicorn.run("rdbase.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":  # pragma: no cover
    main()
```

要点：
- `lifespan` 替代已弃用的 `@app.on_event("startup")`/`"shutdown"`。
- 路由按业务模块拆分到 `routers/`，用 `include_router` 注册。
- 业务逻辑放 `services/` 纯 Python 模块，路由层只做参数校验与调用。

## 路由与参数

### Path / Query / Body 参数

```python
"""用户路由。"""

from __future__ import annotations

from fastapi import APIRouter, Path, Query, status
from rdbase.models.user import UserCreate, UserResponse, UserUpdate

router = APIRouter()


@router.get("/", response_model=list[UserResponse])
def list_users(
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> list[UserResponse]:
    """分页查询用户列表。"""
    # 调用 service 层...
    return []


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int = Path(..., ge=1, description="用户 ID"),
) -> UserResponse:
    """根据 ID 查询单个用户。"""
    # 调用 service 层...
    return UserResponse(id=user_id, name="张三", email="zhang@example.com")


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate) -> UserResponse:
    """创建用户。"""
    # 调用 service 层...
    return UserResponse(id=1, name=user.name, email=user.email)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int = Path(..., ge=1),
    user: UserUpdate = ...,
) -> UserResponse:
    """部分更新用户信息。"""
    # 调用 service 层...
    return UserResponse(id=user_id, name="更新后", email="updated@example.com")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int = Path(..., ge=1)) -> None:
    """删除用户。"""
    # 调用 service 层...
    pass
```

参数类型速查：

| 来源 | 装饰 | 类型 | 示例 |
|------|------|------|------|
| 路径 | `Path(...)` | `int`/`str` | `/{user_id}` |
| 查询 | `Query(...)` | 基本类型/列表 | `?skip=0&limit=20` |
| 请求体 | 自动推断 Pydantic 模型 | `Model` | POST/PUT/PATCH body |
| 表单 | `Form(...)` | `str` | `application/x-www-form-urlencoded` |
| 文件 | `File(...)` | `UploadFile`/`bytes` | `multipart/form-data` |
| Header | `Header(...)` | `str` | `X-Token: abc` |
| Cookie | `Cookie(...)` | `str` | `session_id` |

要点：
- `Query(0, ge=0)`：默认值 + 校验（ge=大于等于，le=小于等于）。
- `Path(...)`：`...` 表示必填（无默认值）。
- `response_model`：自动过滤输出字段，不暴露内部模型。
- `status_code`：显式声明 HTTP 状态码。

## Pydantic 模型

### 请求与响应模型

```python
"""用户相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserBase(BaseModel):
    """用户基础字段（供 Create/Update 继承）。"""

    name: str = Field(..., min_length=1, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱")


class UserCreate(UserBase):
    """创建用户请求体。"""

    password: str = Field(..., min_length=8, description="密码（明文，传输层须 HTTPS）")


class UserUpdate(BaseModel):
    """更新用户请求体（所有字段可选）。"""

    name: str | None = Field(None, min_length=1, max_length=50)
    email: EmailStr | None = None


class UserResponse(UserBase):
    """用户响应模型（不含密码）。"""

    model_config = ConfigDict(from_attributes=True)  # 允许从 ORM 对象构造

    id: int
    created_at: datetime
```

### 嵌套模型与自定义校验

```python
"""通用响应与嵌套模型。"""

from __future__ import annotations

from typing import Generic, TypeVar
from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class Pagination(BaseModel):
    """分页信息。"""

    skip: int = Field(0, ge=0)
    limit: int = Field(20, ge=1, le=100)
    total: int = Field(0, ge=0)


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应包装。"""

    items: list[T]
    pagination: Pagination


class OrderItem(BaseModel):
    """订单项。"""

    product_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1, le=999)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """数量须为正整数。"""
        if v <= 0:
            raise ValueError("数量必须大于 0")
        return v


class Order(BaseModel):
    """订单（嵌套模型）。"""

    order_id: str
    items: list[OrderItem] = Field(..., min_length=1)
```

要点：
- `Field(..., min_length=1, max_length=50)`：`...` 必填 + 长度校验。
- `model_config = ConfigDict(from_attributes=True)`：从 ORM 对象属性构造（替代 v1 `orm_mode`）。
- `field_validator`：自定义字段校验（替代 v1 `@validator`）。
- 请求模型与响应模型分离：响应不含敏感字段（密码）。
- `Generic[T]`：泛型响应包装，复用分页结构。

## 依赖注入

### 基础依赖

```python
"""依赖注入函数。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status


# 简单依赖：公共参数
def common_pagination(
    skip: int = 0,
    limit: int = 20,
) -> dict[str, int]:
    """分页参数依赖，多路由复用。"""
    return {"skip": skip, "limit": limit}


# 认证依赖
def verify_token(authorization: str = Header(...)) -> str:
    """验证 Bearer Token，返回用户标识。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证头",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:]
    # 实际校验 token...
    if token != "valid-token":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或过期",
        )
    return token  # 返回值可供路由使用


# 类型别名：简化路由签名
PaginationDep = Annotated[dict[str, int], Depends(common_pagination)]
TokenDep = Annotated[str, Depends(verify_token)]
```

### 在路由中使用

```python
"""使用依赖的路由。"""

from __future__ import annotations

from fastapi import APIRouter

from rdbase.deps import PaginationDep, TokenDep
from rdbase.models.user import UserResponse

router = APIRouter()


@router.get("/secure", response_model=list[UserResponse])
def list_secure_users(token: TokenDep, pagination: PaginationDep) -> list[UserResponse]:
    """需要认证的分页查询（依赖注入简化签名）。"""
    # token 和 pagination 已由依赖注入解析
    return []
```

### 带缓存的依赖（请求内单例）

```python
"""请求级单例依赖（同请求内只执行一次）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request


def get_db_session(request: Request):
    """从请求状态获取数据库会话（请求内单例）。"""
    if not hasattr(request.state, "db"):
        # 实际创建数据库会话...
        request.state.db = _create_session()
    return request.state.db


DbDep = Annotated[object, Depends(get_db_session)]
# use_cache=True（默认）：同请求内多次 Depends 只执行一次
```

要点：
- `Depends(func)`：函数返回值注入路由参数。
- `Annotated[T, Depends(func)]`：类型别名，简化路由签名（推荐）。
- 依赖可嵌套：依赖 A 依赖 B，FastAPI 自动解析依赖链。
- `use_cache=True`（默认）：同请求内只执行一次，适合 DB 会话。
- 依赖抛 `HTTPException` → 自动转错误响应。

## 中间件

### CORS

```python
"""CORS 中间件配置。"""

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://example.com"],  # 生产环境指定具体域名
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### 自定义中间件

```python
"""请求日志与计时中间件。"""

from __future__ import annotations

import time
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """记录每个请求的方法、路径、耗时与状态码。"""

    async def dispatch(self, request: Request, call_next):
        """拦截请求，记录日志后放行。"""
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


# 注册
app.add_middleware(LoggingMiddleware)
```

## 后台任务

```python
"""后台任务：响应后异步执行（不阻塞响应）。"""

from __future__ import annotations

from fastapi import BackgroundTasks


def send_email(to: str, subject: str, body: str) -> None:
    """发送邮件（模拟耗时操作）。"""
    # 实际发送逻辑...
    pass


def write_log(user_id: int, action: str) -> None:
    """记录操作日志。"""
    # 实际写入逻辑...
    pass


@router.post("/{user_id}/notify")
def notify_user(
    user_id: int,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """创建用户后后台发送邮件 + 记日志（响应不等待）。"""
    background_tasks.add_task(send_email, "user@example.com", "欢迎", "注册成功")
    background_tasks.add_task(write_log, user_id, "registered")
    return {"status": "已调度通知"}
```

要点：
- `BackgroundTasks`：轻量后台任务，响应后同进程执行。
- 适合发邮件、写日志、清理缓存等非关键操作。
- 重型/可靠任务用 Celery/RQ 等消息队列，不用 `BackgroundTasks`。

## 异步处理器

```python
"""异步路由：I/O 密集场景用 async def。"""

from __future__ import annotations

import asyncio
import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/async-fetch")
async def async_fetch(url: str) -> dict[str, str]:
    """异步 HTTP 请求（不阻塞事件循环）。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return {"status_code": str(resp.status_code), "body": resp.text[:200]}


@router.get("/concurrent")
async def concurrent_fetch() -> dict[str, list]:
    """并发请求多个 URL（gather 并行）。"""
    urls = ["https://api.example.com/1", "https://api.example.com/2"]
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for url in urls]
        responses = await asyncio.gather(*tasks)
        return {"statuses": [str(r.status_code) for r in responses]}
```

要点：
- `async def` 路由：I/O 密集（网络/DB 异步驱动）用，不阻塞事件循环。
- `def` 路由：FastAPI 自动放到线程池执行（同步 I/O 也可用）。
- 异步路由内禁止 `time.sleep()`/同步 I/O（阻塞所有协程）。
- `httpx.AsyncClient`：异步 HTTP 客户端（替代 requests）。

## 异常处理

### HTTPException

```python
"""标准 HTTP 异常。"""

from fastapi import HTTPException, status


@router.get("/{user_id}")
def get_user(user_id: int) -> dict:
    """查询用户，不存在时抛 404。"""
    user = _find_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"用户 {user_id} 不存在",
        )
    return user
```

### 自定义异常处理器

```python
"""全局异常处理器：统一错误响应格式。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class BusinessError(Exception):
    """业务逻辑异常。"""

    def __init__(self, code: str, message: str) -> None:
        """初始化业务错误。"""
        self.code = code
        self.message = message


class NotFoundError(Exception):
    """资源不存在异常。"""

    def __init__(self, resource: str, identifier: object) -> None:
        """初始化并记录资源标识。"""
        self.resource = resource
        self.identifier = identifier


app = FastAPI()


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    """业务异常 → 422 统一格式响应。"""
    return JSONResponse(
        status_code=422,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    """资源不存在 → 404 响应。"""
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": f"{exc.resource} {exc.identifier} 不存在"}},
    )
```

要点：
- `HTTPException`：标准 HTTP 错误，FastAPI 内置处理。
- `@app.exception_handler(CustomError)`：自定义异常 → HTTP 响应。
- 统一错误格式：`{"error": {"code": "...", "message": "..."}}`。
- 业务异常与 HTTP 状态码分离：service 层抛业务异常，处理器层转 HTTP。

## 测试（TestClient）

```python
"""FastAPI 应用测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rdbase.app import app


@pytest.fixture
def client() -> TestClient:
    """提供测试客户端（每个测试独立）。"""
    return TestClient(app)


def test_health_check(client: TestClient) -> None:
    """健康检查端点返回 200。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_user(client: TestClient) -> None:
    """创建用户返回 201。"""
    response = client.post(
        "/users/",
        json={"name": "张三", "email": "zhang@example.com", "password": "secure123"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "张三"
    assert "password" not in data  # 响应不含密码


def test_get_user_not_found(client: TestClient) -> None:
    """查询不存在的用户返回 404。"""
    response = client.get("/users/9999")
    assert response.status_code == 404


def test_invalid_input_validation(client: TestClient) -> None:
    """无效输入返回 422 校验错误。"""
    response = client.post("/users/", json={"name": "", "email": "invalid"})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any("name" in str(e.get("loc", [])) for e in errors)


@pytest.mark.slow
def test_concurrent_requests(client: TestClient) -> None:
    """并发请求压力测试（标记 slow）。"""
    import threading

    results: list[int] = []

    def make_request() -> None:
        """发起单个请求。"""
        resp = client.get("/health")
        results.append(resp.status_code)

    threads = [threading.Thread(target=make_request) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(code == 200 for code in results)
```

要点：
- `TestClient`：同步测试 FastAPI 应用（基于 httpx），无需启动服务器。
- `client.get/post/...`：模拟 HTTP 请求，返回 `Response` 对象。
- 校验错误自动返回 422 + `detail` 字段列出具体字段问题。
- fixture 提供 `TestClient`，每个测试独立。
- 压测标记 `@pytest.mark.slow`，CI 默认跳过。

## 静态文件

```python
"""静态文件挂载。"""

from fastapi.staticfiles import StaticFiles

# 挂载静态文件目录（CSS/JS/图片）
app.mount("/static", StaticFiles(directory="static"), name="static")
# 访问：/static/css/style.css → static/css/style.css
```

## 常见陷阱

1. **同步路由阻塞事件循环**：`async def` 路由里做同步 I/O 会阻塞所有协程。用 `def`（自动线程池）或 `run_in_executor`。
2. **响应泄露敏感字段**：直接返回 ORM 对象会暴露所有字段。用 `response_model` 过滤。
3. **不分离请求/响应模型**：密码出现在响应里。请求模型含密码，响应模型不含。
4. **异常处理不统一**：各路由自己拼错误格式。用自定义异常处理器统一。
5. **CORS 配置过于宽松**：`allow_origins=["*"]` + `allow_credentials=True` 不安全。生产环境指定具体域名。
6. **BackgroundTasks 做关键任务**：进程崩溃任务丢失。关键任务用消息队列（Celery/RQ）。
7. **依赖注入不缓存 DB 会话**：同请求内多次创建会话。`use_cache=True`（默认）确保单例。
8. **Pydantic v1/v2 混用**：v2 用 `model_config = ConfigDict()`、`field_validator`，非 v1 的 `Config` 类/`@validator`。
9. **不校验输入范围**：`limit=10000` 拖垮数据库。用 `Query(20, ge=1, le=100)` 限制。
10. **测试启动真实服务器**：`TestClient` 足矣，无需 uvicorn。集成测试用 `httpx.AsyncClient` + ASGI transport。
