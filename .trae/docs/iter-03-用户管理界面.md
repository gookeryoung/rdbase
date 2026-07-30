# 迭代记录 03 - 用户管理界面

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P1 阶段。

- [x] P1-1 用户模型与 JWT 认证（iter-01 后落地）
- [x] P1-2 RBAC 权限：角色枚举与权限依赖、前端路由守卫与按钮级权限（iter-02 完成）
- [x] P1-3 用户管理界面：管理员列表/启用禁用/重置密码/改角色、个人中心改密、前端用户管理页
- [ ] P1-4 测试与文档：accounts 集成测试、API 文档、iter 记录

## 迭代目标

P1-3 用户管理界面：后端提供管理员独占的用户管理接口（列表/启用禁用/重置密码/改角色）与个人改密接口；前端实现用户管理页与个人中心页，路由与菜单按角色控制。

## 改动文件清单

### 后端（backend/）

- `backend/apps/accounts/schemas.py` — 新增 `PasswordResetIn`、`PasswordChangeIn`、`RoleUpdateIn` 三个 Schema
- `backend/apps/accounts/users.py` — **新增**：用户管理 Router
  - `GET /api/v1/users`：用户列表（admin only）
  - `POST /api/v1/users/{id}/toggle-active`：切换启用/禁用（admin only）
  - `POST /api/v1/users/{id}/reset-password`：重置密码（admin only）
  - `PATCH /api/v1/users/{id}/role`：修改角色（admin only）
  - Router 级别 `auth=JWTAuth()` 统一认证，每个路由体首行调用 `require_admin(request)` 校验权限
- `backend/apps/accounts/api.py` — 新增 `POST /api/v1/auth/change-password`：个人改密（校验旧密码后设置新密码，登录用户均可访问）
- `backend/api/v1/__init__.py` — 挂载 `users_router` 到 `/users`

### 前端（frontend/）

- `frontend/src/api/users.ts` — **新增**：用户管理 API 封装（listUsers/toggleUserActive/resetUserPassword/updateUserRole）+ `UserItem` 类型
- `frontend/src/api/auth.ts` — 新增 `changePassword` 函数
- `frontend/src/pages/Users.tsx` — **新增**：用户管理页
  - Table 展示用户列表（ID/用户名/邮箱/角色/状态/操作）
  - Switch 切换启用/禁用
  - Select 行内修改角色
  - Modal 重置密码
  - 角色标签颜色区分（admin 红/designer 蓝/viewer 默认）
- `frontend/src/pages/Profile.tsx` — **新增**：个人中心页
  - Descriptions 展示当前用户信息（用户名/邮箱/角色）
  - Form 修改密码（旧密码/新密码/确认新密码，前端校验两次一致）
- `frontend/src/routes/index.tsx` — 新增 `/users`（RoleRoute admin only）与 `/profile` 路由
- `frontend/src/layouts/MainLayout.tsx` — 菜单新增「用户管理」（admin only，TeamOutlined）与「个人中心」（UserOutlined）

### 测试（tests/）

- `tests/test_accounts_users.py` — **新增**：18 个用例
  - 用户列表：admin 获取成功、viewer/designer 403、未认证 401
  - 切换启用/禁用：禁用/启用成功、用户不存在 404、viewer 403
  - 重置密码：成功（验证新密码可登录）、用户不存在 404、viewer 403
  - 修改角色：成功、无效角色 400、用户不存在 404、viewer 403
  - 个人改密：成功、旧密码错误 400、未认证 401

## 关键决策与依据

1. **django-ninja 无 `dependencies` 参数与 `Depends`**：与 FastAPI 不同，django-ninja 1.6.2 的 Router 与路由装饰器均不支持 `dependencies` 参数，也不导出 `Depends`。改用 Router 级别 `auth=JWTAuth()` 统一认证 + 每个路由体首行调用 `require_admin(request)` 校验权限。复用 P1-2 的 `require_admin` 依赖函数，保持权限逻辑单一来源。
2. **权限校验放函数体而非装饰器**：django-ninja 的依赖注入机制不如 FastAPI 灵活，函数体调用 `require_admin(request)` 最简洁直接，且 `require_admin` 内部已处理未认证（401）与角色不足（403）两种情况。
3. **用户管理 Router 独立文件**：`accounts/users.py` 与 `accounts/api.py`（auth 相关）分离，职责清晰；均挂载到 `api/v1` 的 NinjaAPI 实例。
4. **前端 UserItem 类型独立**：与 `User` 类型字段相同但语义不同（列表项 vs 当前用户），独立定义避免后续字段差异时互相影响。
5. **角色修改用行内 Select**：管理员可直接在 Table 行内切换角色，无需弹窗，操作效率高；重置密码用 Modal（需输入新密码，不适合行内）。
6. **个人中心放侧边栏**：所有角色可见，简单直接；后续可优化为顶部用户下拉菜单（参考 memory 中界面偏好）。
7. **测试辅助函数重构**：`_post`/`_get`/`_patch` 接收 `headers: dict` 参数而非 `**headers`，避免 pyrefly 对 `**_auth_header()` 解包的误判（认为 str 值可能赋给 body 参数）。

## 代码实现情况

- 后端新增 1 个 Router 文件（users.py，82 行）、3 个 Schema、1 个端点（change-password）；`users.py` 100% 覆盖。
- 前端新增 2 个页面（Users.tsx、Profile.tsx）、1 个 API 文件（users.ts）、扩展 1 个 API 文件（auth.ts）；路由与菜单同步更新。
- 测试新增 18 个用例，覆盖所有端点的正常流程、权限边界、错误场景。

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed!
- `uv run ruff format --check backend tests`：33 files already formatted
- `uv run pyrefly check`：0 errors（14 suppressed, 31 warnings not shown）
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：65 passed，coverage 99.37%
  - `tests/test_accounts_users.py`：18 个用例全通过
  - `backend/apps/accounts/users.py`：100% 覆盖
  - `backend/apps/accounts/api.py`：98%（第 106 行 refresh user_id 非 int 分支未覆盖，P1-1 遗留）
- `cd frontend && npm run typecheck`：通过
- `cd frontend && npm run build`：通过（dist 产物 1119.74 kB / gzip 358.88 kB）
- `make check` 全套门禁通过

## 遗留事项

- `backend/apps/accounts/api.py` 第 106 行（refresh 接口 user_id 非 int 分支）仍未覆盖（P1-1 遗留）。
- 前端 ESLint 未配置；前端测试框架未搭建（P1-4 评估）。
- 前端 build 产物超 500 kB 警告（未做代码分割），P5 性能优化阶段处理。
- 个人中心目前在侧边栏，后续可优化为顶部用户下拉菜单。
- 用户管理页未做分页（当前用户量小）；用户量大时需后端分页 + 前端分页组件。

## 下一轮计划

进入 **P1-4 测试与文档**（P1 阶段收尾）：

1. 补全 accounts 模块集成测试：覆盖注册→登录→me→改密→刷新 token 全链路。
2. 补全 `api.py` 第 106 行 refresh user_id 非 int 分支测试，消除覆盖率遗留。
3. API 文档：确认 django-ninja OpenAPI 自动生成可用（`/api/v1/docs` Swagger / `/api/v1/openapi.json`）。
4. 前端测试基础设施评估（vitest + @testing-library/react）。
5. iter-04 记录，P1 阶段收尾总结。
6. P1 阶段交付后自动进入 P2 数据源管理。
