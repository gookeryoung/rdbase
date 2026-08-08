# iter-42：数据集（Dataset）抽象与查询 API

## 需求清单

- [x] 42 数据集（Dataset）抽象与查询 API（req-03 第 42 项）

## 迭代目标

落地 P9 对外 API 的稳定数据访问契约：Dataset 模型（slug 唯一/绑定数据源/表名/字段白名单/
过滤条件/聚合规则/owner/版本化）+ 管理端 CRUD（仅 admin，走 JWTAuth）+ 对外只读查询端点
GET /api/v1/datasets/{slug}/rows（分页/排序/筛选/字段裁剪，走 ApiTokenAuth + scope 校验，
复用 manager 查询能力）+ 前端数据集管理页（admin 可创建/编辑/删除/预览）。为 P9 item 43
（数据集写入 API）与 item 44（调度触发 + Webhook）提供查询底座。

## 改动文件清单

### 新增

- `backend/apps/datasources/datasets_api.py`：Dataset Router（管理 CRUD + 对外只读查询）
- `backend/apps/datasources/migrations/0002_dataset.py`：Dataset 模型迁移
- `backend/apps/audit/migrations/0004_alter_auditlog_action.py`：AuditAction 新增 3 枚举值
- `frontend/src/api/datasets.ts`：前端 API 客户端（列表/详情/创建/更新/删除/预览）
- `frontend/src/pages/Datasets.tsx`：数据集管理页（列表/创建编辑弹窗/预览/启停切换）
- `tests/test_datasources_datasets.py`：54 个测试用例（模型/管理 CRUD/对外查询/权限/边界）

### 修改

- `backend/apps/datasources/models.py`：新增 Dataset 模型（含 increment_version 方法）
- `backend/apps/datasources/schemas.py`：新增 DatasetCreateIn/UpdateIn/Out/ListItemOut/ListOut/
  RowsOut 等请求响应模型
- `backend/apps/datasources/admin.py`：注册 DatasetAdmin（list_display/list_filter/search_fields）
- `backend/apps/audit/models.py`：AuditAction 新增 DATASET_CREATE/UPDATE/DELETE
- `backend/api/v1/__init__.py`：挂载 datasets_router 到 /api/v1/datasets
- `frontend/src/types/index.ts`：新增 Dataset 相关 TypeScript 接口与 AuditAction 更新
- `frontend/src/routes/index.tsx`：新增 /datasets 路由（仅 RoleRoute ADMIN）
- `frontend/src/layouts/MainLayout.tsx`：侧边栏新增「数据集」菜单项（仅 admin 可见）
- `tests/test_audit_models.py`：更新 AuditAction 枚举计数（25→28）与新增值断言

## 关键决策与依据

1. **Dataset 作为对外稳定契约**：外部应用通过 `slug`（如 `user-profiles`）访问数据，
   不感知底层数据源 ID/表名/字段。`fields_whitelist` 实现「列级权限」（空数组表示全部字段），
   `filter_expression` 实现「行级过滤」（与用户传入 filters 取 AND，dataset 配置优先级最高
   避免越权）。复用 req-03 关键决策第 4 条。

2. **管理端与对外端点双 Router 风格**：管理 CRUD 走 `JWTAuth()`（仅 admin，Web 前端使用），
   对外查询走 `ApiTokenAuth()`（外部应用通过 X-API-Token 接入）。两者挂载到同一
   `/api/v1/datasets` 前缀，通过不同 HTTP 方法/路径区分。req-03 关键决策第 3 条「双认证并存」
   在此首次落地。

3. **scope 校验粒度**：对外查询端点要求 Token 含 `datasets:read` scope，使用
   `_require_scope(request, "datasets:read")` 校验。遵循 iter-41 待用户复核项 1 的当前设计
   （全局 scope + Dataset.is_active 控制可见性），未实现按数据集细粒度授权。

4. **filter_expression 优先级高于用户 filters**：`_merge_filters` 合并 dataset 配置与用户
   传入 filters，dataset 配置的键总是覆盖用户传入同名字段（安全优先，防越权绕过行级过滤）。
   例：dataset 配置 `{"is_active": 1}`，用户传 `{"is_active": 0}`，最终取 dataset 的 1。

5. **fields_whitelist 空数组表示全部字段**：`_resolve_columns` 在 whitelist 为空时返回
   None（manager 层查询全部列），非空时校验用户 columns 必须是 whitelist 子集，否则 400。
   避免空数组被误判为「禁止所有列」。

6. **版本化但不自动递增**：`increment_version()` 由调用方显式触发（如后续 item 43 写入时），
   普通 CRUD 更新不自动 +1。避免细粒度编辑频繁递增版本号失去语义。

7. **复用 manager.query_table_rows**：对外查询直接调用现有 P1 manager 的查询能力，支持
   分页/排序/筛选/字段裁剪，无需重写 SQL 构造层。manager 已处理 SQL 注入防护与引擎差异。

8. **前端 RoleRoute 守卫 + 菜单角色过滤**：路由层 `RoleRoute allowedRoles={[Role.ADMIN]}`
   做硬守卫，MainLayout 的 `roles: [Role.ADMIN]` 做菜单可见性过滤，双重防护避免普通用户
   看到入口或直接访问 URL。

## 代码实现情况

- Dataset 模型含 12 个字段 + `increment_version` 方法，3 个索引（is_active/datasource/owner），
  `slug` 唯一约束。
- datasets_api.py 7 个端点：
  - 管理端（JWTAuth + require_admin）：GET "" 列表、POST "" 创建、GET "/{slug}" 详情、
    PATCH "/{slug}" 更新、DELETE "/{slug}" 删除。
  - 对外端（ApiTokenAuth + scope 校验）：GET "/{slug}/rows" 查询行数据。
- `_query_dataset_rows` 辅助函数：解析 columns/filters 参数 → 调用 manager.query_table_rows
  → 返回 (rows, total, returned_columns)。
- `_merge_filters` / `_resolve_columns` / `_require_scope` / `_get_dataset_or_404` /
  `_dataset_to_dict` 5 个辅助函数。
- 3 个审计动作接入 log_audit（resource_type=dataset，含 slug 与 table_name）。
- 前端 Datasets.tsx 约 500 行：列表表格 + 创建/编辑 Modal（含 JSON 校验）+ 预览 Modal
  （动态列渲染）+ 启停 Switch + 删除 Popconfirm。

## 整合优化情况

- 复用 iter-41 的 ApiTokenAuth 与 has_scope 方法，无需新增认证层。
- 复用 accounts.permissions.require_admin 做 RBAC 校验。
- 复用 apps.audit.audit.log_audit 记录业务审计（含哈希链）。
- 复用 manager.query_table_rows 查询能力，不重写 SQL 构造。
- 复用前端 Datasources.tsx 的 Modal + Form + Popconfirm 模式保持一致 UX。
- Dataset 模型复用 DataSource 的 JSONField 默认值模式（default=list / default=dict）。

## 测试验证结果

- 新增 54 个测试（test_datasources_datasets.py）：
  - 模型 6 个（创建/字段默认值/increment_version/slug 唯一约束/字段赋值/字符串表示）
  - 管理 CRUD 12 个（列表/创建/详情/更新/删除/权限校验/重复 slug 400/不存在 404/
    JWT 未登录 401/普通用户 403/部分更新/字段保留）
  - 对外查询 30 个（基础查询/分页/排序/字段裁剪/用户 filters/合并 filters/whitelist
    空表示全部/whitelist 子集校验/dataset 过滤优先级/inactive 404/不存在 404/
    JWT 被拒 401/scope 缺失 403/scope 校验通过/多场景边界与组合用例）
  - 边界 6 个（page=0 自动修正/page_size 上限/order_dir 默认 asc 等组合用例）
- 修复 test_audit_models.py 枚举计数（25→28）并补充 3 个新值断言。
- pyrefly 类型修复：JSONField 的 `in` 运算需 cast(list[str], fields_whitelist)
  显式转型避免类型错误。
- 全套门禁：ruff check + format + pyrefly（0 errors）+ pytest 1475 passed（+54），
  覆盖率 95.48%（未下降）。

## 遗留事项

- 前端未实现 Dataset 版本对比与历史回溯 UI（当前仅展示版本号 Tag）。
- fields_whitelist 的可视化编辑器（当前为 JSON 文本输入），后续可改为从数据源表结构
  自动加载字段列表的多选下拉。
- filter_expression 的可视化构建器（当前为 JSON 文本输入），后续可改为表单式编辑器。
- 对外查询端点未接入速率限制（属 item 45）。
- 未实现 Dataset 软删除（当前为物理删除），后续按需评估。
- 待用户复核项 1（scope 粒度）：当前仍为全局 scope + is_active 控制可见性，未实现按
  数据集细粒度授权。

## 下一轮计划

- iter-43：数据集写入 API。POST /api/v1/datasets/{slug}/rows（单行/批量 UPSERT）+
  冲突策略复用（UPSERT/SKIP/ERROR）+ 写入审计（AuditAction 新增 DATASET_WRITE）+
  配额控制（每 Token 每日写入上限，超额 429）+ 速率限制（Token 维度令牌桶，Redis 实现，
  复用 P8 Redis 基础设施）。
