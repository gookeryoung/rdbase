# 迭代记录 02 - RBAC 权限

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P1 阶段。

- [x] P1-1 用户模型与 JWT 认证：accounts 应用、User 模型（role）、PyJWT、access/refresh 双 token、注册/登录/登出/刷新/me 接口、单元测试（iter-01 后已落地，本轮沿用）
- [x] P1-2 RBAC 权限：角色枚举与权限依赖、前端路由守卫与按钮级权限
- [ ] P1-3 用户管理界面：管理员列表/启用禁用/重置密码、个人中心改密、前端用户管理页
- [ ] P1-4 测试与文档：accounts 集成测试、API 文档、iter-02 记录（本记录）

## 迭代目标

P1-2 RBAC 权限：后端提供基于角色的访问控制依赖，前端实现路由守卫与按钮级权限，并补全登录/登出真实链路，使 RBAC 在前后端贯通生效。

## 改动文件清单

### 后端（backend/）

- `backend/apps/accounts/permissions.py` — **新增**：RBAC 权限依赖模块
  - `require_roles(*allowed_roles)`：工厂函数，返回 django-ninja dependency，校验 `request.auth` 用户角色是否在允许列表中；未认证抛 401，角色不足抛 403
  - `require_admin`：预构造依赖，仅 admin 通过
  - `require_designer_or_admin`：预构造依赖，designer/admin 通过
  - `PermissionDependency` 类型别名，便于类型注解
  - 用法：`@router.get("/x", auth=JWTAuth(), dependencies=[require_admin])`

### 前端（frontend/）

- `frontend/src/types/index.ts` — 新增 `Role` 字符串枚举（admin/designer/viewer，与后端 `Role.TextChoices` 对齐）；`User` 接口增加 `role: Role` 与 `is_active?`；`LoginResponse` 改为 `{ access, user }`
- `frontend/src/api/auth.ts` — **新增**：auth API 封装（login/logout/refresh/fetchMe）
- `frontend/src/utils/permission.ts` — **新增**：前端权限工具
  - `hasRole(user, ...roles)`：判断用户是否拥有指定角色之一
  - `isAdmin(user)` / `isDesignerOrAdmin(user)`：便捷判断
- `frontend/src/components/RoleRoute.tsx` — **新增**：角色路由守卫
  - 未登录跳 `/login`；已登录但角色不足显示 antd `Result` 403 页面
  - `allowedRoles=[]` 时仅校验登录状态（等价 ProtectedRoute）
- `frontend/src/components/Permission.tsx` — **新增**：按钮级权限组件
  - 用户角色不在 `allowedRoles` 中则渲染 `fallback`（默认 null）
- `frontend/src/pages/Login.tsx` — 接入真实 `/auth/login` 接口，登录成功写入 auth store 并跳转主页；错误提示从 `response.data.detail` 提取
- `frontend/src/layouts/MainLayout.tsx` — 菜单按角色过滤（数据库设计仅 admin/designer 可见）；登出调用 `/auth/logout` 后端清理 cookie；菜单 items 映射为 antd 接受的结构（剥离 `roles` 字段避免 TS 类型不匹配）
- `frontend/src/routes/index.tsx` — `/designer` 路由用 `RoleRoute` 包裹（allowedRoles=[ADMIN, DESIGNER]）；保留 `ProtectedRoute` 作为根登录守卫

### 测试（tests/）

- `tests/test_accounts_permissions.py` — **新增**：RBAC 权限依赖单元测试，12 个用例
  - `require_roles` 工厂：空参数抛 ValueError、返回可调用对象、每次返回新闭包实例
  - 授权通过：admin/designer 各自放行
  - 拒绝未授权：viewer 访问 admin 端点抛 403
  - 拒绝未认证：`request.auth=None` 或非 User 实例抛 401
  - 预构造依赖 `require_admin` / `require_designer_or_admin` 行为验证

## 关键决策与依据

1. **后端 RBAC 用 dependencies 而非自定义 HttpBearer 子类**：JWTAuth 仅做认证（who you are），权限校验用 `dependencies=[require_roles(...)]` 关注授权（what you can do），职责分离；dependency 函数从 `request.auth` 取已认证用户，复用 JWTAuth 结果，避免重复解码 token。
2. **`require_roles` 工厂模式**：django-ninja dependencies 是 list[Callable]，工厂返回闭包支持运行时指定角色集合；预构造 `require_admin` / `require_designer_or_admin` 避免重复书写常见组合。
3. **前端 Role 用字符串枚举**：与后端 `Role.TextChoices` 的字符串值对齐；JSON.stringify 后枚举值即字符串，localStorage 反序列化无需额外转换，运行时 `user.role === Role.ADMIN` 等价于 `"admin" === "admin"`。
4. **前端 RoleRoute 与 ProtectedRoute 共存**：ProtectedRoute 仅校验登录（根路由用），RoleRoute 同时校验登录与角色（受限子路由用）；语义清晰，避免单一组件承担多重职责。
5. **菜单过滤而非隐藏路由**：菜单按角色过滤提升体验（viewer 看不到「数据库设计」入口），但路由层仍用 RoleRoute 守卫防止直接访问 URL 绕过；双层防护符合安全最佳实践。
6. **Login.tsx 真实接入**：P1-1 仅完成接口，登录页是占位；本轮补全真实调用链路，使 RBAC 可端到端验证（登录 → token → 角色判断 → 路由守卫）。
7. **MainLayout 菜单 items 映射**：antd Menu 的 `items` 类型要求 MenuItemType 或 SubMenuType，自定义 `MenuItem` 含 `roles` 字段会被识别为 SubMenuType 但缺 `children`；映射剥离 `roles` 后类型匹配。

## 代码实现情况

- 后端 `permissions.py` 21 行，单一职责，完整 docstring 与类型注解；`__all__` 显式导出。
- 前端新增 4 个文件（api/auth.ts、utils/permission.ts、components/RoleRoute.tsx、components/Permission.tsx），修改 4 个文件（types、Login、MainLayout、routes），均含中文注释。
- 后端 RBAC 与 JWTAuth 解耦：`auth=JWTAuth()` 完成认证，`dependencies=[require_admin]` 完成授权，组合使用。
- 前端三层权限：路由守卫（RoleRoute）+ 菜单过滤（filterMenuItems）+ 按钮级（Permission 组件，供后续 P1-3 用户管理页使用）。

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed!
- `uv run ruff format --check backend tests`：31 files already formatted
- `uv run pyrefly check`：0 errors（12 suppressed, 28 warnings not shown）
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：47 passed，coverage 99.20%
  - `tests/test_accounts_permissions.py`：12 个用例全通过，`permissions.py` 100% 覆盖
- `cd frontend && npm run typecheck`：通过
- `cd frontend && npm run build`：通过（dist 产物 761.55 kB / gzip 249.04 kB）
- `make check` 全套门禁通过

## 遗留事项

- `backend/apps/accounts/api.py` 第 106 行（refresh 接口 user_id 非 int 分支）未覆盖，属 P1-1 范围，本轮未处理。
- 前端尚未配置 ESLint（package.json 有 lint 脚本但未安装 eslint 配置），待 P1-4 或后续统一接入。
- Permission 组件尚未在业务页面使用（待 P1-3 用户管理页落地）。
- 前端测试基础设施未搭建（无 vitest/jest），P1-4 评估是否引入。

## 下一轮计划

进入 **P1-3 用户管理界面**：

1. 后端：新增 `accounts/users.py` Router（管理员列表/启用禁用/重置密码/改角色），挂载到 `/api/v1/users`，全部用 `auth=JWTAuth(), dependencies=[require_admin]` 保护。
2. 后端：新增个人中心改密接口 `/api/v1/auth/change-password`，登录用户均可访问。
3. 前端：新增 `pages/Users.tsx` 用户管理页（Table + 启用禁用 Switch + 重置密码 Modal），路由用 `RoleRoute allowedRoles=[ADMIN]`。
4. 前端：新增 `pages/Profile.tsx` 个人中心改密页。
5. 测试：accounts 集成测试覆盖用户管理接口与权限边界（admin 可操作、viewer 403）。
6. 文档：iter-03 记录、API 文档（django-ninja OpenAPI 自动生成）。
