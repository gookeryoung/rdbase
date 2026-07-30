# 迭代记录 04 - P1 测试补全与收尾

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P1 阶段收尾事项。

- [x] 01 补全 api.py refresh user_id 非 int 分支测试，消除覆盖率遗留
- [x] 02 验证 `make check` 全套门禁通过
- [x] 03 确认 OpenAPI 文档可用（`/api/v1/docs` Swagger）
- [x] 04 补充 accounts 集成测试（注册→登录→me→改密→刷新 全链路）
- [x] 05 写 iter-04 记录，P1 阶段收尾总结并提交

## 迭代目标

P1 阶段最终收尾：补齐边界分支测试、验证 API 文档可访问性、覆盖端到端认证流程，确保整套门禁 100% 通过后提交。

## 改动文件清单

### 测试（tests/）

- `tests/test_accounts_api.py` — 新增 `test_refresh_with_non_int_user_id_returns_401`，覆盖 `api.py` 第 106 行 `user_id` 非 int 分支
- `tests/test_api_health.py` — 增强 `test_api_v1_openapi_json_available` 断言（验证 auth/users 路由出现在 paths 中），新增 `test_api_v1_swagger_docs_available` 验证 Swagger UI HTML 页面可访问
- `tests/test_accounts_flow.py` — 新建集成测试文件，3 个用例：
  - `test_full_auth_flow_register_login_me_change_refresh_logout`：注册→登录→/me→改密→刷新→登出全链路
  - `test_login_with_new_password_after_change`：改密后新密码登录成功、旧密码登录失败
  - `test_change_password_wrong_old_returns_400`：旧密码错误返回 400

## 关键决策与依据

1. **不启用 ReDoc**：NinjaAPI 默认仅挂载 Swagger UI（`/api/v1/docs`），ReDoc 需显式配置 `docs=Redoc()`。P1 阶段 Swagger 已满足开发调试需求，遵循"不写未被要求的功能"原则不引入。
2. **集成测试用同一 Client 实例**：复用 `django.test.Client` 以保持 cookie 状态，模拟真实浏览器的 refresh token cookie 流转。
3. **改密接口 401 验证**：集成测试中先验证不带 token 访问 `/change-password` 返回 401，再验证带 token 改密成功，覆盖认证与业务两条路径。
4. **OpenAPI schema 路由断言**：在原有 schema 元信息断言基础上，增加 `/api/v1/auth/login` 与 `/api/v1/users` 路径存在性断言，确保两个 Router 均已正确聚合。

## 代码实现情况

- `test_refresh_with_non_int_user_id_returns_401` 直接构造 `user_id="not-int"` 的 refresh token，绕过 `create_refresh_token` 工厂，覆盖原本难以触及的分支。
- `test_accounts_flow.py` 通过 `_post`/`_get` 辅助函数统一请求构造，支持可选 `token` 参数附加 `Authorization: Bearer` 头。
- 改密接口的 POST+Header 场景直接使用 `client.post(..., HTTP_AUTHORIZATION=...)`，因为辅助函数签名未暴露 headers 参数，保持辅助函数简洁。

## 整合优化情况

- 无重复代码：集成测试与现有单元测试关注点不同（端到端 vs 单点），未提取共享 fixture。
- `test_api_health.py` 的 OpenAPI 测试断言增强后，仍保持单文件 3 个测试的清晰结构。

## 测试验证结果

`make check` 全套通过：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 34 files already formatted
- `pyrefly check` — 0 errors (15 suppressed, 34 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 70 passed, coverage 100.00%

测试分布：

| 文件 | 用例数 |
|------|--------|
| test_accounts_api.py | 21 |
| test_accounts_flow.py | 3 |
| test_accounts_models.py | 6 |
| test_accounts_permissions.py | 12 |
| test_accounts_users.py | 18 |
| test_accounts_jwt.py | 5 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **70** |

## 遗留事项

- 前端 `npm install` 与端到端联调验证留待 P2 阶段（数据源管理）启动时处理。
- 生产环境 SECRET_KEY 强度校验（当前 dev settings 的 12 字节 key 触发 `InsecureKeyLengthWarning`）在 P5 部署阶段统一处理。
- Sphinx 文档更新留待 P5 文档汇总阶段。

## 下一轮计划

P1 用户与权限阶段已交付完毕，进入 **P2 数据源管理** 阶段：

1. **P2 收集**：扫描需求清单中数据源相关需求；调用 `python-standards`、`python-file-io` SKILL；研究 SQLAlchemy 多数据库连接模式。
2. **P2 计划**：拆分子任务
   - 创建 `apps/datasources` 应用
   - 数据源模型（name/engine/host/port/database/credentials）
   - SQLAlchemy 引擎工厂与连接池
   - 数据源 CRUD API（admin 创建/designer 查看）
   - 凭据加密存储（cryptography）
   - 前端数据源管理页面
   - 测试覆盖率 ≥ 95%
3. **P2 实现→测试→文档→验证**：六步迭代循环。
