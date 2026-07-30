# 迭代记录 05 - P2 数据源管理

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P2 阶段。

- [x] 10 数据源模型与加密：DataSource 模型、Fernet 加密、连接配置 Schema
- [x] 11 SQLAlchemy 连接引擎池：按数据源动态创建引擎、连接测试、健康检查
- [x] 12 数据源 CRUD 接口与界面：列表/新增/编辑/删除/测试连接、分组标签
- [x] 13 P2 测试与文档：datasources 模块测试、连接池 Mock 策略、文档更新

## 迭代目标

P2 数据源管理里程碑：可连接外部数据库。完成数据源模型、Fernet 凭据加密、SQLAlchemy 引擎池、CRUD 接口与前端管理界面，配套测试覆盖率 100%。

## 改动文件清单

### 后端（backend/）

- `backend/apps/datasources/__init__.py` — 应用包
- `backend/apps/datasources/apps.py` — DatasourcesConfig
- `backend/apps/datasources/models.py` — DataSource 模型（name/engine/host/port/database/username/password_encrypted/group/tags/is_active/created_by/created_at/updated_at）+ EngineType 枚举 + set_password/get_password 方法
- `backend/apps/datasources/crypto.py` — Fernet 加解密工具：derive_key（SHA256 派生）、encrypt_password、decrypt_password
- `backend/apps/datasources/engine.py` — SQLAlchemy 引擎池：ConnectionConfig 值对象、build_config、get_engine（带缓存）、verify_connection、dispose_engine/dispose_all
- `backend/apps/datasources/schemas.py` — Pydantic Schema：DataSourceCreateIn/UpdateIn/Out、TestConnectionIn/Out、MessageOut
- `backend/apps/datasources/api.py` — CRUD Router：list/create/retrieve/update/delete/test_saved/test_temp
- `backend/apps/datasources/admin.py` — DataSourceAdmin
- `backend/apps/datasources/migrations/0001_initial.py` — 迁移
- `backend/api/v1/__init__.py` — 挂载 datasources_router 到 `/datasources`
- `backend/rdbase/settings/base.py` — 启用 `apps.datasources`

### 前端（frontend/）

- `frontend/src/types/index.ts` — 追加 EngineType 枚举、DataSource/Create/Update、TestConnection/Result 类型
- `frontend/src/api/datasources.ts` — 新建：list/create/retrieve/update/delete/testSaved/testTemp 接口封装
- `frontend/src/pages/Datasources.tsx` — 新建：数据源管理页面（Table + Modal 表单 + 测试连接 + Popconfirm 删除）
- `frontend/src/routes/index.tsx` — 注册 `/datasources` 路由

### 测试（tests/）

- `tests/test_datasources_crypto.py` — 6 用例：密钥派生、加解密 round-trip、空明文、错误密钥、损坏 token、同明文不同 token
- `tests/test_datasources_models.py` — 7 用例：MySQL/SQLite 创建、名称唯一、默认值、__str__、set/get_password、admin 注册
- `tests/test_datasources_engine.py` — 12 用例：URL 构造（sqlite/mysql/postgresql/无端口/不支持）、build_config、引擎缓存、连接成功/失败、dispose_engine/dispose_all
- `tests/test_datasources_api.py` — 19 用例：列表（viewer 可读/未认证 401）、创建（admin 成功/viewer 403/重名 400/无效引擎 400/无密码）、详情（成功/404）、更新（admin 成功/viewer 403/重名 400/切引擎）、删除（admin/viewer 403）、测试连接（已保存/临时 admin/viewer 403/无效引擎）

## 关键决策与依据

1. **Fernet 密钥派生**：用 `SHA256(SECRET_KEY)` 取前 32 字节 → urlsafe_base64 作为 Fernet key，复用 Django SECRET_KEY 无需额外配置。同一明文每次加密产生不同 token（Fernet 内置随机 IV），安全性更高。
2. **DataSource.objects 显式类型注解**：pyrefly strict 模式不识别 Django 动态注入的 `objects` manager，用 `objects: models.Manager[DataSource]` 类注解解决，运行时无副作用。
3. **schemas.py 延迟创建**：P2-1 阶段先删除未使用的 schemas.py 避免 0% 覆盖率，P2-3 实现 API 时再创建，遵循"不为未来预留"原则。
4. **`/test` 路由顺序**：django-ninja 按注册顺序匹配，`/test` 必须在 `/{ds_id}` 之前注册，否则被路径参数拦截返回 405。
5. **引擎缓存模块级 dict**：按数据源 ID 缓存 Engine 实例避免重复创建；update/delete 时主动 dispose_engine 失效缓存。
6. **SQLite 连接测试用 :memory:**：避免 mock 复杂性，用真实 SQLite 内存库做端到端连接验证，测试可靠且快速。
7. **verify_connection 命名**：避免 `test_` 前缀被 pytest 误收集为测试用例。
8. **前端权限分层**：列表/详情/测试已保存连接对所有登录用户开放（designer/viewer 可浏览可测试），创建/更新/删除/测试临时连接仅 admin，与后端 RBAC 一致。
9. **SQLite 表单联动**：前端 Modal 中 engine 选 sqlite 时隐藏 host/port/username/password 字段，遵循"关联设计"原则简化界面。

## 代码实现情况

- 后端完整：模型 + 加密 + 引擎池 + CRUD API + 测试，6 个模块职责清晰（models/crypto/engine/schemas/api/admin）。
- 前端完整：类型 + API 封装 + 管理页面 + 路由，参考 Users.tsx 风格，tsc 类型检查通过。
- 测试策略：单元测试（crypto/models/engine）+ 接口测试（api），用 SQLite 内存库做真实连接测试，无需 mock。

## 整合优化情况

- crypto.py 与 engine.py 按职责拆分为独立模块，避免合并为杂物间。
- ConnectionConfig 用 `@dataclass(frozen=True)` 值对象，不可变且可哈希。
- 测试用 `_clear_engine_cache` autouse fixture 避免缓存跨测试污染。

## 测试验证结果

`make check` 全套通过：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 48 files already formatted
- `pyrefly check` — 0 errors (24 suppressed, 47 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 114 passed, coverage 100.00%

测试分布：

| 文件 | 用例数 |
|------|--------|
| test_accounts_api.py | 21 |
| test_accounts_flow.py | 3 |
| test_accounts_models.py | 6 |
| test_accounts_permissions.py | 12 |
| test_accounts_users.py | 18 |
| test_datasources_api.py | 19 |
| test_datasources_crypto.py | 6 |
| test_datasources_engine.py | 12 |
| test_datasources_models.py | 7 |
| test_accounts_jwt.py | 5 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **114** |

## 遗留事项

- 前端 `npm install` 与端到端联调验证留待 P3 阶段启动时处理。
- MySQL/PostgreSQL 真实连接测试需配置实际数据库，当前仅 SQLite 内存库测试。
- 数据源分组与标签的前端筛选/管理界面未实现（当前仅在表单中可填，列表展示但无筛选）。
- dev settings 的 12 字节 SECRET_KEY 触发 `InsecureKeyLengthWarning`，P5 部署阶段统一处理。

## 下一轮计划

P2 数据源管理阶段已交付完毕，进入 **P3 数据库设计** 阶段：

1. **P3 收集**：扫描需求清单中数据库设计相关需求；调用 `python-standards` SKILL；研究 SQLAlchemy inspect 反射模式。
2. **P3 计划**：拆分子任务
   - 创建 `apps/designer` 应用
   - SQLAlchemy inspect 反射库/Schema/表/字段元数据
   - 表设计器后端：字段 Schema、DDL 生成器（CREATE/ALTER 多方言）
   - 设计草稿模型与版本
   - 前端表设计器界面（字段编辑表格、类型选择、索引面板、DDL 预览）
   - React Flow ER 图与连线同步 DDL
   - 测试覆盖率 ≥ 95%
3. **P3 实现→测试→文档→验证**：六步迭代循环。
