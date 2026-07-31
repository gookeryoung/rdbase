# 迭代记录 14 - P5 审计日志

## 需求清单

来源：`.trae/req/req-01-数据库管理平台.md` P5 阶段任务 25。

- [x] 25 审计日志：操作拦截中间件、日志模型、查询界面、导出

## 迭代目标

实现 P5-1 审计日志（任务 25）：双层审计架构（中间件层 + 业务层）、审计日志模型、查询/详情/导出 API、前端管理界面（筛选/分页/详情抽屉/CSV 导出）、各业务模块（datasources/designer/manager）写操作接入 `log_audit`、权限控制（仅管理员可访问审计日志）、全量测试覆盖。

## 改动文件清单

### 后端 - 新增 audit 应用（backend/apps/audit/）

- `apps/audit/__init__.py` — 应用包初始化
- `apps/audit/apps.py` — AppConfig（`default_auto_field = BigAutoField`）
- `apps/audit/models.py` — `AuditLog` 模型 + 三个枚举：
  - `AuditAction`（17 个值：write/login/logout/datasource.*/draft.*/ddl.apply/dml.*/sql.execute/obj.*）
  - `AuditSource`（middleware/business）
  - `AuditStatus`（success/failure）
  - 字段：user/username/action/source/status/method/path/resource_type/resource_id/datasource_id/datasource_name/sql/row_count/elapsed_ms/ip/user_agent/error_message/extra(JSON)/created_at
  - 5 个索引（user/action/datasource/created_at/status）
  - `ordering = ["-id"]`（最新在前）
- `apps/audit/middleware.py` — `AuditMiddleware` 中间件：
  - 拦截 POST/PATCH/PUT/DELETE 写操作
  - 排除路径：`/health`、`/api/v1/docs`、`/api/v1/openapi`、`/static/`、`/admin/jsi18n/`
  - 记录通用维度：用户/IP/UA/方法/路径/状态码/耗时
  - 审计失败不阻断业务（捕获异常 + logger.exception）
  - `_get_client_ip` 优先级：X-Forwarded-For → X-Real-IP → REMOTE_ADDR
- `apps/audit/audit.py` — `log_audit` 业务层辅助函数：
  - 显式 keyword-only 参数（action/status/resource_type/resource_id/datasource_id/datasource_name/sql/row_count/elapsed_ms/error_message/extra）
  - SQL 截断到 `_MAX_SQL_LENGTH = 4096`
  - 失败返回 None（不抛异常）
- `apps/audit/schemas.py` — Pydantic Schema：`AuditLogOut`/`AuditLogListOut`/`AuditLogDetailOut`/`MessageOut`
- `apps/audit/api.py` — audit Router（3 个接口，仅管理员）：
  - `GET /audit/logs`：分页查询，支持 10 个筛选条件（user_id/username/action/source/status/resource_type/datasource_id/path/start/end）
  - `GET /audit/logs/export`：CSV 流式导出（UTF-8 BOM、1000 行分块、Content-Disposition 带时间戳）
  - `GET /audit/logs/{log_id}`：单条详情
  - **关键**：`/logs/export` 必须注册在 `/logs/{log_id}` 之前，否则 ninja 会将 "export" 当作 log_id 解析
- `apps/audit/admin.py` — Django Admin 注册（只读、不可新增/修改、可查看）
- `apps/audit/migrations/0001_initial.py` — 初始迁移

### 后端 - 业务模块接入 log_audit

- `backend/apps/datasources/api.py` — 3 处 log_audit 调用（create/update/delete 数据源）
- `backend/apps/designer/api.py` — 6 处 log_audit 调用（draft create/update/delete/rollback、ddl apply、其他）
- `backend/apps/manager/api.py` — 22 处 log_audit 调用：
  - 行 CRUD：insert/update/delete（各 3 处：2 个失败分支 + 1 个成功）
  - SQL 执行：3 处（2 失败 + 1 成功）
  - 导入：3 处（2 失败 + 1 成功）
  - 对象管理：view/routine/trigger 的 alter/drop（共 10 处）

### 后端 - 配置

- `backend/api/v1/__init__.py` — 挂载 audit_router 到 `/audit`
- `backend/rdbase/settings/base.py` — INSTALLED_APPS 添加 `apps.audit`、MIDDLEWARE 添加 `apps.audit.middleware.AuditMiddleware`

### 前端

- `frontend/src/types/index.ts` — 新增 5 个类型：`AuditAction`（17 个值联合）、`AuditSource`、`AuditStatus`、`AuditLog`、`AuditLogQuery`、`AuditLogList`
- `frontend/src/api/audit.ts` — 3 个 API 函数：`listAuditLogs`/`retrieveAuditLog`/`exportAuditLogs`（含 Blob 下载与 Content-Disposition 文件名解析）
- `frontend/src/pages/AuditLogs.tsx` — 审计日志管理页面：
  - 筛选栏：用户名/动作/来源/状态/资源类型/数据源 ID/路径/时间范围
  - 表格：ID/时间/用户/动作（彩色 Tag）/来源/状态/方法/路径/数据源/SQL（Tooltip）/影响行数/耗时/IP
  - 操作：详情（Drawer 展示完整字段）、导出 CSV、刷新
  - 分页：默认 20 条/页，可选 10/20/50/100
- `frontend/src/layouts/MainLayout.tsx` — 菜单项「审计日志」（仅 admin 可见）
- `frontend/src/routes/index.tsx` — `/audit` 路由（RoleRoute 限制 admin）

### 测试（tests/）

- `tests/test_audit_models.py` — 6 + 9 = 15 个测试：模型字段默认值、ordering、indexes、__str__、AuditAction/AuditSource/AuditStatus 枚举、Admin 权限
- `tests/test_audit_middleware.py` — 10 + 15 = 25 个测试：
  - `_is_excluded` 5 个分支（health/docs/static/admin/jsi18n/normal）
  - `_get_client_ip` 5 个分支（xff/xff+空格/xreal_ip/remote_addr/empty）
  - 中间件集成：POST 触发记录、GET 不记录、DELETE 触发、4xx 记录为 failure、排除路径不记录、未认证写仍记录、中间件异常不阻断业务
  - `log_audit` 辅助：正常返回 AuditLog、超长 SQL 截断、异常返回 None、extra=None 存空 dict、_get_client_ip 4 个分支
- `tests/test_audit_api.py` — 29 个测试：
  - list 分页与 10 个筛选条件（user_id/username/action/source/status/resource_type/datasource_id/path/start/end）
  - retrieve 详情（存在/不存在 404）
  - export CSV（含 BOM、流式响应、文件名、列顺序）
  - 权限：admin 可访问、designer/viewer 403、未认证 401
  - 参数校验：page<1 / page_size<1 返回 400
- `tests/test_audit_integration.py` — 11 个集成测试：
  - 数据源 create/update/delete 各记录一条 business 日志
  - 草稿 create/update/delete/rollback 各记录一条
  - DDL apply 记录
  - 行 insert/update/delete 记录
  - SQL execute 记录
  - 双层审计：一次 POST 产生 middleware + business 各一条
  - 失败路径（4xx）也记录 business 日志

### 需求与文档（.trae/）

- `.trae/req/req-01-数据库管理平台.md` — 任务 25 标 [x]
- `.trae/docs/iter-14-P5审计日志.md` — 本迭代记录

## 关键决策与依据

1. **双层审计架构**：中间件层记录通用 HTTP 维度（用户/IP/方法/路径/状态码/耗时），业务层记录业务维度（SQL/影响行数/资源类型/数据源 ID）。两者通过 `source` 字段区分，独立存储不重复。中间件层覆盖所有写操作（兜底），业务层补充详细上下文（精准）。
2. **审计失败不阻断业务**：`log_audit` 与中间件均捕获异常后 logger.exception，不抛出。审计日志属于辅助功能，不应影响主业务流程。
3. **SQL 截断**：`_MAX_SQL_LENGTH = 4096`，避免单条日志过大（如批量 INSERT 可能数千行）。
4. **路由注册顺序**：`/logs/export` 必须在 `/logs/{log_id}` 之前注册，否则 ninja 将 "export" 当作 log_id 解析导致 422。
5. **CSV 流式导出**：使用 `StreamingHttpResponse` + 1000 行分块查询，避免一次性加载全部到内存（大数据量场景）。UTF-8 BOM 确保 Excel 正确识别中文。
6. **权限分层**：审计日志查询/导出仅 admin 可访问（`require_admin`）；前端 `RoleRoute allowedRoles={[Role.ADMIN]}` + 菜单项仅 admin 可见。
7. **IP 提取优先级**：X-Forwarded-For 第一个 → X-Real-IP → REMOTE_ADDR。中间件与 audit.py 各有一份 `_get_client_ip`（避免跨模块依赖，符合内聚原则）。
8. **datasource_name 类型注解为 `Any`**：解决 pyrefly 对 `str | None` 与 Django CharField 默认值 `""` 的类型识别问题。
9. **时间筛选支持无时区**：`_parse_datetime` 对 naive datetime 调用 `timezone.make_aware` 处理，避免 Django `RuntimeWarning: DateTimeField received a naive datetime`。
10. **`# noqa: PLR0913`**：`log_audit` 与 `_filter_qs` 参数超过 5 个，但都是必要的业务字段，无法进一步聚合，显式标注抑制 ruff 警告。

## 代码实现情况

### 模型设计

`AuditLog` 模型采用「宽表」设计，所有审计相关字段集中在一表中，通过 `source` 字段区分来源。相比拆分两表（middleware_log + business_log），宽表设计优势：

- 查询时无需 UNION，前端筛选条件统一
- 关联索引（user/action/datasource/created_at/status）覆盖主要查询模式
- 业务层记录的 `method`/`path`/`ip`/`user_agent` 字段与中间件重复，但便于业务日志独立查询（不依赖中间件记录）

### 中间件实现

```python
class AuditMiddleware:
    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        start = time.perf_counter()
        response = self.get_response(request)
        if request.method in _AUDITED_METHODS and not _is_excluded(request.path):
            try:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                _record_middleware_audit(request, response, elapsed_ms)
            except Exception:
                logger.exception("中间件记录审计日志失败 path=%s", request.path)
        return response
```

- 使用 `time.perf_counter()` 而非 `time.time()`，避免系统时钟回拨影响耗时计算
- 仅在响应阶段记录（可获取状态码）
- 排除路径采用前缀匹配（`/health` 匹配 `/health` 与 `/health/`）

### 业务层接入

各业务 view 在写操作的关键路径显式调用 `log_audit`：

- **成功路径**：操作完成后、return 前调用 `log_audit(status=SUCCESS, ...)`
- **失败路径**：except 分支调用 `log_audit(status=FAILURE, error_message=str(exc), ...)`

以 manager 行新增为例：

```python
@router.post("/{ds_id}/tables/{table_name}/rows")
def create_row_view(request, ds_id, table_name, payload):
    try:
        row = insert_row(...)
    except (SQLAlchemyError, QueryError) as exc:
        log_audit(request, action=AuditAction.DML_INSERT,
                  status=AuditStatus.FAILURE, error_message=str(exc), ...)
        raise HttpError(400, ...) from None
    log_audit(request, action=AuditAction.DML_INSERT,
              resource_type="row", resource_id=str(row.get("id")),
              sql="<INSERT>", row_count=1, ...)
    return JsonResponse(row, status=201)
```

## 整合优化情况

1. **`_get_client_ip` 双份实现**：middleware.py 与 audit.py 各有一份，避免跨模块依赖。两份逻辑完全一致，未来若需统一可提取到 audit/utils.py（当前遵循「内聚优先，不过早抽象」原则）。
2. **`_filter_qs` 抽取**：list 与 export 接口的筛选逻辑完全一致，抽取为 `_filter_qs` 辅助函数，避免重复。
3. **`_log_dict` 统一**：list/retrieve/export 三个接口的响应字典构造统一用 `_log_dict`，确保字段顺序与类型一致。

## 测试验证结果

- `uv run ruff check backend tests`：通过
- `uv run ruff format --check backend tests`：88 文件已格式化
- `uv run pyrefly check`：0 errors（76 suppressed, 112 warnings not shown）
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：625 测试全绿，覆盖率 98.75%
  - `backend/apps/audit/api.py`：99%（1 行未覆盖，line 75 为 `_parse_datetime` 的 ValueError 分支）
  - `backend/apps/audit/audit.py`：100%
  - `backend/apps/audit/middleware.py`：100%
  - `backend/apps/audit/models.py`：100%
  - `backend/apps/audit/schemas.py`：100%
  - `backend/apps/audit/admin.py`：100%
  - `backend/apps/manager/api.py`：93%（31 行未覆盖，均为 SQLAlchemyError 错误分支，总覆盖率满足 95% 门禁）
  - `backend/apps/designer/api.py`：100%
  - `backend/apps/datasources/api.py`：100%
- `npm run typecheck`：通过

## 遗留事项

- `backend/apps/audit/api.py` line 75（`_parse_datetime` 的 `ValueError` 分支）未覆盖：该分支为防御性代码（解析失败返回 None），可通过传入非法时间字符串触发，但当前测试已覆盖空字符串与合法时间，价值较低，暂不补测。
- `backend/apps/manager/api.py` 覆盖率 93%：31 行未覆盖均为 SQLAlchemyError 错误分支（如 export 过程异常、delete_routine/routine/trigger 的 SQLAlchemyError），不影响 95% 总门禁。
- `sample_demo.db` 为 P4 阶段测试数据文件，位于项目根目录，未纳入 .gitignore，本次提交不包含此文件。
- 浏览器端到端手动测试未执行（依赖单元/接口测试与 typecheck）。

## 下一轮计划

进入 P5-2 系统设置（任务 26）：会话超时、密码策略、数据源加密轮换界面。预计涉及：

- 新增 `apps/settings` 应用（或扩展 accounts）：系统设置模型（键值对存储）
- 会话超时配置（JWT access token TTL 可配置化）
- 密码策略配置（最小长度/复杂度/历史密码检查）
- 数据源加密密钥轮换界面（重新加密所有数据源密码到新密钥）
- 前端系统设置页面（仅 admin 可访问）
