# 迭代记录 01 - 架构搭建

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P0 阶段。

- [x] 01 切换后端依赖：移除 fastapi/uvicorn，引入 django/django-ninja/sqlalchemy/uvicorn，重构 pyproject.toml 与工具链配置
- [x] 02 Django 项目初始化：项目包、分环境 settings、ASGI/WSGI、根 URL，django-ninja API 挂载
- [x] 03 React 前端初始化：Vite + React + TS + Ant Design + Zustand + axios，登录页占位、路由骨架
- [x] 04 前后端联调基础：CORS、代理（vite proxy）、健康检查接口、登录接口占位
- [x] 05 迁移 src/rdbase：删除原 FastAPI app.py，平台元信息迁入 backend，更新 Makefile/CI

## 迭代目标

P0 架构搭建里程碑：可运行的空壳 + CI 通过。完成前后端骨架与工具链配置，为 P1 用户与权限阶段奠定基础。

## 改动文件清单

### 后端（backend/）

- `backend/manage.py` — Django 管理入口
- `backend/rdbase/__init__.py` — 项目包，含 `__version__ = "0.1.0"`
- `backend/rdbase/settings/{__init__,base,dev,prod}.py` — 分环境 settings
  - `base.py`：INSTALLED_APPS 含 corsheaders，本地 apps 注释待启用；中间件含 CorsMiddleware；SQLite 默认数据库；zh-hans / Asia/Shanghai
  - `dev.py`：DEBUG=True，ALLOWED_HOSTS=["*"]，CORS_ALLOW_ALL_ORIGINS=True
  - `prod.py`：从环境变量读取 SECRET_KEY/DATABASES/ALLOWED_HOSTS/CORS
- `backend/rdbase/{asgi,wsgi}.py` — ASGI/WSGI 入口
- `backend/rdbase/urls.py` — `/health/` 健康检查 + `/api/v1/` 挂载 NinjaAPI + `/admin/`
- `backend/api/__init__.py`、`backend/api/v1/__init__.py` — NinjaAPI 实例（title="rdbase API", version="1.0.0"）
- `backend/apps/__init__.py` — 占位，P1+ 创建具体应用

### 前端（frontend/）

- `frontend/package.json` — React 18 + TS 5 + Vite 5 + Ant Design 5 + Zustand 5 + axios + react-router-dom 6 + reactflow 11
- `frontend/vite.config.ts` — proxy `/api`、`/health` → `http://localhost:8000`
- `frontend/tsconfig.json`、`frontend/tsconfig.node.json`、`frontend/vite-env.d.ts`
- `frontend/index.html`、`frontend/main.tsx`、`frontend/App.tsx`
- `frontend/src/api/client.ts` — axios 实例封装
- `frontend/src/store/auth.ts` — Zustand auth store
- `frontend/src/layouts/MainLayout.tsx` — 主布局（Ant Design Layout）
- `frontend/src/pages/{Login,Dashboard}.tsx` — 登录页占位、Dashboard 占位
- `frontend/src/routes/index.tsx` — 路由配置
- `frontend/src/components/ProtectedRoute.tsx` — 路由守卫
- `frontend/src/types/index.ts` — TS 类型定义
- `frontend/.gitignore`

### 工具链

- `pyproject.toml` — requires-python>=3.10, Django>=5.2,<6.0, django-ninja, django-cors-headers, sqlalchemy, mysqlclient, psycopg[binary], cryptography, uvicorn[standard], gunicorn；optional-dependencies lint/test + dependency-groups dev
- `ruff.toml` — target py310, exclude frontend
- `pyrefly.toml` — search-path backend
- `pytest.ini` — DJANGO_SETTINGS_MODULE=rdbase.settings.dev, pythonpath=backend
- `.coveragerc` — source=backend, 排除 settings/asgi/wsgi/manage.py/migrations
- `.bumpversion.toml` — 指向 backend/rdbase/__init__.py
- `Makefile` — make check/lint/typecheck/cov/migrate/run-be/run-fe/dev/tox/bump
- `tox.ini` — py310-py313
- `.github/workflows/ci.yml` — Python 3.10 & 3.13
- `Dockerfile`、`.python-version`（3.13）、`.dockerignore`

### 删除

- `src/rdbase/app.py`、`src/rdbase/__init__.py`、`src/rdbase/py.typed` — 原 FastAPI 骨架移除

### 测试

- `tests/test_rdbase.py` — 版本与导入测试
- `tests/test_api_health.py` — 健康检查 + OpenAPI schema 测试（用 `isinstance` + `json.loads` 避免 pyrefly WSGIRequest 误报）

### 文档

- `README.md` — 反映 django-ninja + React 架构，移除 FastAPI/PyPI 发布相关内容

## 关键决策与依据

1. **后端框架切换 FastAPI → Django + django-ninja**：需求文档明确选型；Django 自带 ORM/Auth/Admin 适合管理平台；django-ninja 提供 FastAPI 风格 API 与 Pydantic Schema。
2. **平台数据库 SQLite（开发）/ PostgreSQL（生产）**：开发期零配置；生产用 PostgreSQL 通过环境变量切换。
3. **多数据库连接层用 SQLAlchemy 2.x**：统一抽象 MySQL/PostgreSQL/SQLite 反射与 SQL 执行，与 Django ORM 解耦。
4. **API 聚合模式**：NinjaAPI 实例在 `backend/api/v1/__init__.py`，各业务模块 Router 在 app 内定义后 import 聚合。
5. **测试用 `isinstance` + `json.loads`**：避免 pyrefly 对 `WSGIRequest` 的属性访问误报，保持类型检查零错误。
6. **覆盖率排除 settings/asgi/wsgi/manage.py/migrations**：这些文件无业务逻辑，不参与覆盖率统计。
7. **前端未 npm install**：P0 仅交付骨架文件，npm install 留待 P1 联调时做，避免 CI 无谓安装。

## 代码实现情况

- 后端骨架完整可运行：`make run-be` 启动后 `/health/`、`/api/v1/openapi.json`、`/admin/` 均可访问。
- 前端骨架完整：路由守卫、登录页占位、Dashboard 占位、axios 客户端、Zustand auth store、Vite proxy 配置齐备。
- 工具链完整：lint / typecheck / cov / migrate / run-be / run-fe / tox / bump 命令齐备。
- CI 配置：Python 3.10 与 3.13 双版本测试。

## 测试验证结果

- `uv sync` 成功
- `ruff check backend tests` 通过
- `ruff format --check backend tests` 通过
- `pyrefly check` 0 错误
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` 4 个测试通过，覆盖率 100%
- `make check` 全套门禁通过

## 遗留事项

- 前端 `npm install` 未执行（P1 联调时做）
- Sphinx 文档（`docs/`）尚未更新为 django-ninja + React 架构（P5 文档汇总阶段统一处理）
- `apps/` 目录下具体业务应用未创建（P1 起按阶段创建）

## 下一轮计划

进入 **P1 用户与权限** 阶段（里程碑：可登录、RBAC 生效）：

1. **P1 收集**：扫描需求清单，调用 `python-standards`、`python-fastapi`（django-ninja 风格相近，参考 Router/Schema 模式）SKILL；查阅 Django auth 与 django-ninja JWT 文档。
2. **P1 计划**：拆分子任务
   - 创建 `apps/accounts` 应用，启用 INSTALLED_APPS
   - 扩展 User 模型（如需额外字段）或使用默认 `django.contrib.auth.models.User`
   - 实现注册/登录/登出/刷新 token 接口（django-ninja JWT，HttpOnly Cookie）
   - RBAC 角色模型（admin/designer/viewer）与权限装饰器
   - 前端登录页对接、路由守卫与按钮级权限
   - 用户管理界面（管理员列表/启用禁用/重置密码、个人中心改密）
   - accounts 模块单元/接口测试，覆盖率 ≥ 95%
3. **P1 实现→测试→文档→验证**：六步迭代循环，默认 3~5 轮。
