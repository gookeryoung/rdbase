# 迭代记录 06 - P3 数据库设计后端

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P3 阶段。

- [x] 14 元数据反射：基于 SQLAlchemy inspect 提供库/Schema/表/字段元数据读取
- [x] 15 表设计器后端：字段 Schema、DDL 生成器（CREATE/ALTER 多方言）、设计草稿模型与版本
- [ ] 16 表设计器前端：字段编辑表格、类型选择、索引面板、DDL 预览
- [ ] 17 关系设计与 ER 图：外键/多对多配置、React Flow ER 图、连线同步 DDL
- [ ] 18 P3 测试与文档收尾

## 迭代目标

P3 数据库设计里程碑（后端部分）：完成元数据反射与可视化建表后端能力。交付 SQLAlchemy inspect 反射层、Pydantic 字段 Schema、多方言 DDL 生成器、设计草稿与版本模型及对应 CRUD API，测试覆盖率 ≥ 95%。

## 改动文件清单

### 后端（backend/）

- `backend/apps/designer/__init__.py` — 应用包
- `backend/apps/designer/apps.py` — DesignerConfig
- `backend/apps/designer/inspector.py` — SQLAlchemy 反射层：ColumnMeta/TableMeta/ForeignKeyMeta/IndexMeta 值对象，list_databases/list_schemas/list_tables/list_views/inspect_table 函数
- `backend/apps/designer/models.py` — DesignDraft 模型（name/datasource/table_name/schema_name/spec/status/created_by）+ DesignVersion 模型（draft/version_no/spec/created_by）+ DraftStatus 枚举
- `backend/apps/designer/schemas.py` — Pydantic Schema：FieldSpec/IndexSpec/ForeignKeySpec/TableDesignSpec（表设计规范）+ DraftCreateIn/UpdateIn/Out + VersionOut + DDLPreviewIn/Out/DDLExecuteIn/Out + DatabaseOut/TableOut/ColumnOut（反射输出）
- `backend/apps/designer/ddl.py` — DDL 生成器：generate_create_table/generate_alter_table/generate_ddl，支持 MySQL/PostgreSQL/SQLite 三方言
- `backend/apps/designer/api.py` — Router：反射接口（databases/schemas/tables/table detail）+ 草稿 CRUD + 版本管理 + DDL 预览/执行
- `backend/apps/designer/admin.py` — DesignDraftAdmin/DesignVersionAdmin
- `backend/apps/designer/migrations/0001_initial.py` — 迁移
- `backend/api/v1/__init__.py` — 挂载 designer_router 到 `/designer`
- `backend/rdbase/settings/base.py` — 启用 `apps.designer`

### 测试（tests/）

- `tests/test_designer_inspector.py` — 14 用例：SQLite 内存库反射（databases/schemas/tables/views/inspect_table/字段元数据/主键/外键/索引/注释/空库）
- `tests/test_designer_ddl.py` — 42 用例：CREATE TABLE（三方言基本生成、schema 前缀、DEFAULT、COMMENT、外键、索引、复合主键、单列非自增主键内联、PostgreSQL SERIAL 替换、显式 SERIAL 类型、错误分支）+ ALTER TABLE（重命名、增删改字段、MySQL MODIFY、PostgreSQL ALTER COLUMN 拆分、SQLite 不支持修改、索引增删、外键增删、无名外键跳过、表注释变更/清空、无变更返回空）+ generate_ddl 统一入口
- `tests/test_designer_api.py` — 15 用例：反射接口（databases/schemas/tables/table detail/未认证 401/数据源不存在 404）
- `tests/test_designer_drafts_api.py` — 29 用例：草稿 CRUD（创建 201/viewer 403/重名 400/数据源 404/列表/按数据源过滤/详情/404/更新创建新版本/重名 400/重命名成功/删除 200/viewer 403）+ 版本管理（列表/回滚创建新版本/404）+ DDL 预览（CREATE/ALTER/数据源 404/非法 spec 400/未认证 401）+ DDL 执行（建表成功/viewer 403/404/执行失败 400/DDL 生成错误 400/old_spec ALTER）

## 关键决策与依据

1. **反射层独立模块**：inspector.py 封装 SQLAlchemy inspect API，返回 frozen dataclass 值对象，屏蔽方言差异，便于缓存与序列化。
2. **DDL 生成器纯函数**：generate_create_table/generate_alter_table 不依赖数据库连接，仅根据 spec + dialect 生成 SQL 字符串，便于测试与预览。
3. **单列主键内联 PRIMARY KEY**：单列主键（不论是否自增）一律内联到列定义，复合主键才独立输出 PRIMARY KEY 子句。SQLite 的 AUTOINCREMENT 必须跟在 PRIMARY KEY 后，通过 inline_pk 标志控制。
4. **PostgreSQL SERIAL 替换**：PostgreSQL 自增主键用 SERIAL/BIGSERIAL/SMALLSERIAL 替换 INTEGER/BIGINT/SMALLINT，避免显式序列。用户显式指定 SERIAL 类型时原样输出。
5. **PostgreSQL ALTER COLUMN 拆分**：PostgreSQL 修改字段时拆分为 ALTER COLUMN TYPE / SET NOT NULL / DROP NOT NULL / SET DEFAULT / DROP DEFAULT 多条语句，符合 PG 语法。MySQL 用 MODIFY COLUMN 重写整列，SQLite 不支持修改字段定义（抛 DDLOperationNotSupported）。
6. **表注释方言差异**：MySQL 内联 `COMMENT '...'` 或 `ALTER TABLE ... COMMENT = '...'`；PostgreSQL 独立 `COMMENT ON TABLE/COLUMN` 语句；SQLite 忽略注释。PG 清空注释时不生成语句（不支持空注释）。
7. **草稿版本自动管理**：创建草稿时自动生成 v1 快照，更新草稿时自动创建新版本号（取最大版本号 +1），回滚版本时把指定版本 spec 作为新版本保存，保证版本链可追溯。
8. **草稿唯一性校验**：按 (datasource, table_name, schema_name) 三元组校验唯一，更新时排除自身。schema_name 为空字符串与 None 视为相同（默认 ""）。
9. **DDL 执行事务保证**：apply 接口用 `engine.begin()` 上下文，所有语句在同一事务内执行，失败回滚。执行成功后草稿状态置为 applied。
10. **权限分层**：反射接口与版本列表/DDL 预览对所有登录用户可读（designer/viewer 可浏览）；草稿 CRUD/版本回滚/DDL 执行仅 designer 或 admin。
11. **路由顺序**：`/ddl/preview` 必须在 `/{draft_id}` 之前注册；`/drafts/{draft_id}/versions/{version_no}/rollback` 等子资源路由在 `/drafts/{draft_id}` 之后。django-ninja 按注册顺序匹配。
12. **无名外键差异跳过**：ALTER 时无名外键无法精确删除，跳过不生成 DROP 语句（CREATE 时原样输出）。

## 代码实现情况

- **inspector.py**：5 个公开函数（list_databases/list_schemas/list_tables/list_views/inspect_table）+ 4 个 frozen dataclass（ColumnMeta/ForeignKeyMeta/IndexMeta/TableMeta）。SQLite 返回 `['main']` 作为 database/schema 列表。
- **ddl.py**：3 个公开函数（generate_create_table/generate_alter_table/generate_ddl）+ 2 个异常类（DDLError/DDLOperationNotSupported）+ DDLResult 值对象。内部辅助函数处理标识符引用、类型格式化、字段定义、外键子句、索引语句等。
- **models.py**：2 个模型（DesignDraft/DesignVersion）+ DraftStatus 枚举（DRAFT/APPLIED/ARCHIVED）。DesignDraft.spec 用 JSONField 存储表设计规范。
- **schemas.py**：FieldSpec 含 name/type/length/nullable/default/comment/primary_key/unique/autoincrement 9 字段；TableDesignSpec 含 name/schema_name/comment/fields/indexes/foreign_keys。所有 Schema 用 ninja.Schema 基类。
- **api.py**：反射接口 4 个（databases/schemas/tables/table detail）+ 草稿 CRUD 5 个 + 版本管理 2 个 + DDL 预览/执行 2 个，共 13 个端点。

## 整合优化情况

- inspector/ddl/models/schemas/api 按职责拆分为独立模块，避免合并为杂物间。
- DDL 生成器纯函数设计，无副作用，易于测试与组合。
- 值对象统一用 `@dataclass(frozen=True)`，不可变且可哈希。
- 测试用 SQLite 内存库做真实反射与执行测试，无需 mock，可靠且快速。
- `_clear_engine_cache` autouse fixture 避免引擎缓存跨测试污染。

## 测试验证结果

`make check` 全套通过：

- `ruff check backend tests` — All checks passed
- `ruff format --check backend tests` — 62 files already formatted
- `pyrefly check` — 0 errors (44 suppressed, 71 warnings not shown)
- `pytest -m "not slow" --cov=backend --cov-fail-under=95` — 212 passed, coverage 99.72%

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
| test_designer_api.py | 15 |
| test_designer_ddl.py | 42 |
| test_designer_drafts_api.py | 29 |
| test_designer_inspector.py | 14 |
| test_accounts_jwt.py | 5 |
| test_api_health.py | 3 |
| test_rdbase.py | 2 |
| **合计** | **212** |

模块覆盖率：

| 模块 | 覆盖率 |
|------|--------|
| backend/apps/designer/inspector.py | 100% |
| backend/apps/designer/schemas.py | 100% |
| backend/apps/designer/api.py | 100% |
| backend/apps/designer/ddl.py | 99%（仅 MySQL 复合主键自增列分支未覆盖，罕见场景） |
| backend/apps/designer/models.py | 95%（__str__ 方法未覆盖） |

## 遗留事项

- 前端 `npm install` 与端到端联调验证留待 P3-3 启动时处理。
- ddl.py 第 151 行（MySQL 复合主键中的自增列）未覆盖，属罕见场景，可接受。
- models.py 的 `__str__` 方法（admin 显示用）未覆盖，Django 模型惯例不写测试。
- MySQL/PostgreSQL 真实连接测试需配置实际数据库，当前仅 SQLite 内存库测试。
- dev settings 的 12 字节 SECRET_KEY 触发 `InsecureKeyLengthWarning`，P5 部署阶段统一处理。

## 下一轮计划

P3-1（反射层）与 P3-2（表设计器后端）已交付完毕，进入 **P3-3 表设计器前端**：

1. **P3-3 收集**：扫描需求清单中前端相关需求；研究 Ant Design Table 可编辑行、Select 类型选择、Modal 索引面板组件模式。
2. **P3-3 计划**：拆分子任务
   - 设计器 API client 封装（drafts/versions/ddl preview/apply）
   - 草稿列表页 + 草稿编辑器（字段编辑表格、类型选择、索引面板、外键面板、DDL 预览）
   - 反射接口联动（选数据源 → 加载库/Schema/表）
   - DDL 预览面板（实时生成 CREATE/ALTER 语句）
3. **P3-3 实现→测试→文档→验证**：六步迭代循环。
