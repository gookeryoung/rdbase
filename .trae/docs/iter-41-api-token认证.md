# iter-41：API Token 认证机制

## 需求清单

- [x] 41 API Token 认证机制（req-03 第 41 项）

## 迭代目标

落地 P9 数据中心对外 API 的认证底座：ApiToken 模型 + 生成/校验/吊销/轮换 +
ApiTokenAuth 双头认证中间件 + /api/v1/tokens CRUD（仅 admin）+ 接入 P8 幂等 key
抽象（Token 自动作为幂等主体）。为后续数据集查询/写入/调度触发端点提供鉴权基础。

## 改动文件清单

### 新增

- `backend/apps/accounts/tokens_api.py`：Token 管理 Router（创建/列表/详情/吊销/轮换）
- `backend/apps/accounts/migrations/0003_apitoken.py`：ApiToken 模型迁移
- `backend/apps/audit/migrations/0003_alter_auditlog_action.py`：AuditAction 新增 3 枚举值
- `tests/test_accounts_api_tokens.py`：40 个测试用例（模型/认证/Router/幂等主体）

### 修改

- `backend/apps/accounts/models.py`：新增 ApiToken 模型（generate/rotate/is_valid/touch_last_used/has_scope）
- `backend/apps/accounts/auth.py`：新增 ApiTokenAuth（X-API-Token 优先，回退 Bearer）
- `backend/apps/accounts/schemas.py`：新增 ApiTokenCreateIn/ApiTokenOut/ApiTokenListItemOut/ApiTokenListOut/ApiTokenRotateOut
- `backend/apps/accounts/admin.py`：注册 ApiTokenAdmin
- `backend/apps/audit/models.py`：AuditAction 新增 TOKEN_CREATE/TOKEN_REVOKE/TOKEN_ROTATE
- `backend/api/v1/__init__.py`：挂载 /api/v1/tokens 路由
- `backend/apps/system/idempotency.py`：get_idempotent_subject 支持 token:{prefix} 主体
- `tests/test_system_idempotency.py`：补充 token 主体回退用例
- `tests/test_audit_models.py`：更新 AuditAction 枚举计数（22→25）与新增值断言

## 关键决策与依据

1. **ApiToken 模型放 accounts 应用**：Token 与 User 强关联（created_by 外键），
   认证逻辑与 JWT 同属 accounts，避免新建 datacenter 应用增加跨模块依赖。
   req-03 数据模型章节将 ApiToken 列为「apps/datasources 或新 apps/datacenter」，
   但实际放 accounts 更符合现有架构（auth/jwt/users 均在此）。

2. **明文仅创建时返回，DB 存 SHA-256 哈希**：`secrets.token_urlsafe(32)` 生成约 43
   字符 URL 安全明文，`hash_plaintext` 计算 SHA-256 十六进制存库；`prefix` 取前 8 位
   仅用于列表展示识别（无法还原明文）。泄露后可吊销（is_active=False）或轮换
   （覆盖哈希，旧明文立即失效）。

3. **ApiTokenAuth 双头优先级**：X-API-Token 头优先（外部应用惯用），未携带时回退
   Authorization: Bearer。任一头存在但校验失败均返回 401，**不回退到另一形式**，
   避免认证绕过。继承 HttpBearer 复用 OpenAPI scheme 文档生成。

4. **request.auth = User, request.api_token = ApiToken**：ApiTokenAuth 认证成功后
   将创建者 User 挂到 request.auth（与 JWTAuth 一致，复用 require_admin/log_audit），
   同时将 ApiToken 实例挂到 request.api_token 供幂等层识别主体。

5. **幂等主体抽象落地**：get_idempotent_subject 检查 request.api_token，若是 ApiToken
   实例则返回 `token:{prefix}`，否则回退 `user:{pk}`。用 isinstance 严格判断避免
   MagicMock 测试替身误判。req-03 关键决策第 2 条「为 P9 铺路」在此闭环。

6. **CRUD 仅 admin**：遵循 req-03「仅 admin」要求，普通用户无法管理 Token。Token
   创建后任何持有明文的应用均可用于 ApiTokenAuth 认证（P9 后续端点）。

7. **touch_last_used 用 UPDATE 而非 save**：避免触发模型信号与 full_save 开销，
   直接 `filter(pk=).update(last_used_at=)` 并同步内存对象。

## 代码实现情况

- ApiToken 模型含 6 个方法：hash_plaintext（静态）、generate（类方法）、rotate、
  is_valid、touch_last_used、has_scope。
- ApiTokenAuth 实现 `__call__`（双头优先级）与 `authenticate`（哈希查表+校验）。
- tokens_api.py 5 个端点：POST 创建、GET 列表、GET 详情、POST 吊销、POST 轮换。
- 3 个审计动作接入 log_audit（resource_type=api_token，含 prefix 与 scopes）。
- 2 个迁移文件由 makemigrations 自动生成并 ruff format 格式化。

## 整合优化情况

- 复用 P8 幂等层 get_idempotent_subject 抽象，无需重构幂等存储/manager。
- 复用 accounts.permissions.require_admin 做 RBAC 校验。
- 复用 apps.audit.audit.log_audit 记录业务审计（含哈希链）。
- ApiTokenAuth 继承 ninja.security.HttpBearer 复用 OpenAPI scheme 文档生成。

## 测试验证结果

- 新增 40 个测试（test_accounts_api_tokens.py）：模型 12 + 认证类 11 + Router 13 + 幂等 4。
- 补充 test_system_idempotency.py 2 个用例（MagicMock 回退、显式 None 回退）。
- 修复 test_audit_models.py 枚举计数（22→25）并补充 3 个新值断言。
- 修复 test_system_idempotency.py 预存 E711 违规（== None → is None）。
- 全套门禁：ruff check + format + pyrefly（0 errors）+ pytest 1421 passed（+42），
  覆盖率 95.52%（+0.08%，未下降）。

## 遗留事项

- 前端 Token 管理页（列表/创建/吊销/查看 last_used_at）属 item 45，本期不涉及。
- ApiTokenAuth 尚未在任何业务端点启用（P9 item 42 数据集查询端点将首次使用）。
- Token 维度速率限制属 item 45，本期仅认证不限流。
- 待用户复核项 1（scope 粒度）：当前设计为全局 scope（datasets:read/write/sync:trigger），
  按 Dataset.is_active 控制可见性，未实现按数据集细粒度授权。

## 下一轮计划

- iter-42：数据集（Dataset）抽象与查询 API。Dataset 模型 + GET /api/v1/datasets/{slug}/rows
  （走 ApiTokenAuth + scope 校验，复用 manager 查询能力）+ 前端数据集管理页。
