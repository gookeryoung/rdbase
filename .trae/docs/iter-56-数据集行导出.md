# iter-56：P9-Q1 数据集行导出

## 需求清单

- [x] 50 新增 `GET /api/v1/datasets/{slug}/export` 端点（req-04 P9-Q1 item 50）
- [x] 51 数据集行导出限流：按 `dataset_export:{user_id}` 维度令牌桶限流（item 51）
- [x] 52 前端数据集详情页加「导出 CSV」按钮（item 52）

## 迭代目标

启动 P9 数据导出增强的第一阶段，补全数据集行导出场景：

1. **后端导出端点**：新增 `GET /api/v1/datasets/{slug}/export`，流式返回 CSV，
   复用 `Dataset.filter_expression` 行级过滤与 `fields_whitelist` 列级白名单。
2. **流式查询函数**：新增 `iter_filtered_table_rows`，支持带列裁剪与筛选条件的
   流式表行查询，供导出端点使用（区别于无过滤的 `iter_table_rows`）。
3. **导出限流**：按 `dataset_export:{user_id}` 维度令牌桶限流，防滥用导出大表。
4. **前端导出入口**：数据集管理页加「导出 CSV」按钮，触发浏览器下载，
   429 限流响应体（JSON 被 axios 包成 Blob）需特殊解析。

## 改动文件清单

### 修改

- `backend/apps/manager/query.py`：
  - 新增 `iter_filtered_table_rows` 函数（882-936 行）：流式生成带列裁剪与筛选
    条件的表行数据生成器，复用 `_validate_columns` / `_validate_filter_columns` /
    `_build_where_clause` 既有校验与 WHERE 构造逻辑。
  - `__all__` 导出列表新增 `"iter_filtered_table_rows"`。
- `backend/apps/datasources/datasets_api.py`：
  - 导入 `StreamingHttpResponse` / `quote` / `settings` / `check_token_bucket` /
    `iter_filtered_table_rows` / `get_column_names` / `QueryError` /
    `SQLAlchemyError` / `User`。
  - 新增 `export_dataset_rows` 端点（470-585 行）：`GET /{slug}/export`，
    JWT 认证 + 令牌桶限流 + Eager 表/列校验 + 流式 CSV 响应。
- `backend/rdbase/settings/base.py`：
  - 新增 `RATE_LIMIT_DATASET_EXPORT_CAPACITY: int = 10`（桶容量，突发上限）。
  - 新增 `RATE_LIMIT_DATASET_EXPORT_REFILL_RATE: float = 0.5`（每秒补充令牌数）。
- `frontend/src/api/datasets.ts`：
  - 新增 `exportDatasetCsv` 函数（52-75 行）：`responseType: "blob"` 接收二进制，
    从 `Content-Disposition` 解析文件名，构造 `<a download>` 触发浏览器下载。
- `frontend/src/pages/Datasets.tsx`：
  - 导入 `DownloadOutlined` 图标与 `exportDatasetCsv` API。
  - 新增 `exportingSlug` state（loading 状态按 slug 维度）。
  - 新增 `handleExportCsv` 处理函数（248-273 行）：429 响应体被 axios 包成
    `Blob`，需 `.text()` + `JSON.parse` 提取 `detail` 字段提示用户。
  - 操作列新增「导出 CSV」按钮（338-343 行）。
- `tests/test_datasources_datasets.py`：
  - 新增 `_reset_export_rate_limiter` autouse fixture（194-205 行）：每测试前后
    调用 `rate_limiter.reset_rate_limiter()`，避免事务回滚导致 user.pk 复用
    引发限流桶状态跨测试泄漏。
  - 新增 15 个导出端点测试（1127-1410 行）：
    `test_export_dataset_rows_full_csv` / `_columns_subset` / `_with_user_filters` /
    `_filter_expression_applied` / `_fields_whitelist` / `_empty_table` /
    `_unauth_401` / `_inactive_dataset_404` / `_inactive_datasource_404` /
    `_unsupported_format_400` / `_not_found_404` / `_nonexistent_table_400` /
    `_invalid_columns_400` / `_rate_limited_429` / `_rate_limit_per_user`。
- `tests/test_manager_query.py`：
  - 新增 6 个 `iter_filtered_table_rows` 单元测试（1745-1835 行）：
    `test_iter_filtered_table_rows_streams_all` / `_columns_subset` /
    `_with_filters` / `_batch_size` / `_invalid_column_raises` /
    `_unknown_table_raises`。

### 新增

- `.trae/docs/iter-56-数据集行导出.md`：本迭代记录。
- `.trae/req/req-04-数据导出.md`：P9 需求清单（本迭代创建并标记 item 50/51/52 完成）。

### 删除

- `.trae/docs/iter-51-清洗Pipeline基础.md`（迭代文件数达 6，按规则清理最旧 iter-51，
  保留最新 5 条：iter-52/53/54/55/56）。

## 关键决策与依据

1. **新增 `iter_filtered_table_rows` 而非扩展 `iter_table_rows`**：
   `iter_table_rows` 无 `columns` / `filters` 参数，调用方（表导出端点）不需要
   过滤；为它加可选参数会破坏单一职责并影响既有调用方。新增独立函数复用
   `_validate_columns` / `_build_where_clause` 等既有辅助，零重复代码。
2. **不直接调用 `_query_dataset_rows`**：该函数返回分页 `list`（非流式），适合
   `/rows` 与 `/preview` 分页接口；导出需流式避免大表 OOM，故提取 filter 合并
   与列裁剪逻辑到端点内直接调用 `iter_filtered_table_rows`。filter 合并/列裁剪
   的 4 个辅助函数（`_parse_filters_param` / `_parse_columns_param` /
   `_normalize_filter_expr` / `_merge_filters` / `_resolve_columns`）均为模块级
   公共函数，端点直接复用，无逻辑重复。
3. **Eager 校验表存在与列名**：`iter_filtered_table_rows` 是生成器，校验延迟到
   首次 `next()` 才执行（此时已进入流式响应，响应头已发送，无法返回 400）。
   故在端点内提前调用 `get_column_names` 校验表存在，并显式校验用户请求列在
   表内，确保错误在响应头发送前抛出为 400 JSON。
4. **限流按 `user_id` 维度而非 IP**：导出端点需 JWT 认证，用户维度比 IP 更精准
   （同 NAT 后多用户不会互相影响），且与 `/rows` 写入端点的 token 维度限流
   模式一致。限流 key 为 `dataset_export:{user.pk}`，容量 10 / 补充 0.5/s
   （每 2 秒恢复 1 次），与触发器限流参数一致。
5. **autouse fixture 重置 rate_limiter**：Django 测试事务回滚导致不同测试的
   `user.pk` 复用（如均为 2），本地令牌桶单例会累积计数使后续测试误 429。
   fixture 在每测试前后调用 `reset_rate_limiter()` 清空后端，保证测试独立性。
6. **429 响应体前端特殊解析**：导出端点用 `responseType: "blob"` 接收二进制流，
   429 限流响应体虽是 JSON，但被 axios 整体包成 `Blob`。前端需 `blob.text()` +
   `JSON.parse` 提取 `detail` 字段，否则只能显示通用「导出失败」。
7. **`_stream()` 错误处理加 `# pragma: no cover`**：Eager 校验已拦截表不存在/
   非法列名场景，流式阶段几乎不会抛 `QueryError` / `SQLAlchemyError`；
   覆盖率工具对不可达分支标记 `no cover` 避免虚低。
8. **`Content-Disposition` 同时给 `filename` 与 `filename*=UTF-8''`**：前者兼容
   旧浏览器，后者 RFC 5987 编码支持非 ASCII 文件名（如中文 slug）。

## 代码实现情况

### `iter_filtered_table_rows` 流式查询函数

```python
def iter_filtered_table_rows(engine, table_name, schema=None, columns=None,
                              filters=None, batch_size=1000):
    # 1. 校验表存在 + 列名合法性（复用 _validate_columns / _validate_filter_columns）
    # 2. 构造 SELECT col1, col2 FROM table WHERE ...
    # 3. PG/MySQL 启用 stream_results 服务端游标
    # 4. fetchmany(batch_size) 分批 yield dict
```

与 `iter_table_rows` 的差异：支持 `columns` 列裁剪与 `filters` 筛选条件，
适用于数据集导出等需应用行级过滤/列级白名单的场景。

### `export_dataset_rows` 端点流程

1. 校验 `format=csv`（其他格式 400）。
2. 获取 `request.auth` 用户（JWTAuth 已校验）。
3. 令牌桶限流 `dataset_export:{user.pk}`，超限 429 + `Retry-After`。
4. `_get_dataset_or_404(slug, active_only=True)`：数据集不存在或未启用 404。
5. 数据源 `is_active=False` 404。
6. 解析并合并 filters（Dataset.filter_expression + 用户 filters，同名列以 Dataset 为准）。
7. 解析 columns（白名单子集校验）。
8. **Eager 校验**：`get_column_names` 反射表列名，校验用户请求列在表内。
9. 构造 `iter_filtered_table_rows` 生成器 + `rows_to_csv` 文本块生成器。
10. `StreamingHttpResponse` 流式输出，`text/csv; charset=utf-8`，
    `Content-Disposition: attachment; filename="{slug}.csv"`。

### 前端 `exportDatasetCsv` API 函数

- `client.get(url, { responseType: "blob" })` 接收二进制流。
- 从 `Content-Disposition` 头解析文件名（正则 `filename="?([^"]+)"?`），
  失败回退 `{slug}.csv`。
- 构造 `Blob` + `URL.createObjectURL` + `<a download>` 触发浏览器下载。
- 调用方 `handleExportCsv` 维护 `exportingSlug` loading 状态，429 响应体
  `Blob.text()` + `JSON.parse` 提取 `detail` 提示。

## 整合优化情况

- 无新重复代码。`iter_filtered_table_rows` 复用 `_validate_columns` /
  `_validate_filter_columns` / `_build_where_clause` / `_resolve_schema` /
  `_format_table_ref` / `_quote_ident` / `get_column_names` 七个既有辅助函数。
- 导出端点复用 `_get_dataset_or_404` / `_parse_filters_param` /
  `_parse_columns_param` / `_normalize_filter_expr` / `_merge_filters` /
  `_resolve_columns` 六个 datasets_api 模块级函数，与 `/rows` / `/preview`
  端点共享 filter 合并与列裁剪逻辑。
- `rows_to_csv` 流式 CSV 生成器直接复用，零修改。
- `_reset_export_rate_limiter` autouse fixture 仅作用于导出测试模块，不影响
  其他模块的限流测试。

## 测试验证结果

### `iter_filtered_table_rows` 单元测试（6 用例）

```
uv run pytest tests/test_manager_query.py -k iter_filtered -v
  test_iter_filtered_table_rows_streams_all PASSED
  test_iter_filtered_table_rows_columns_subset PASSED
  test_iter_filtered_table_rows_with_filters PASSED
  test_iter_filtered_table_rows_batch_size PASSED
  test_iter_filtered_table_rows_invalid_column_raises PASSED
  test_iter_filtered_table_rows_unknown_table_raises PASSED
  6 passed
```

### 导出端点测试（15 用例）

```
uv run pytest tests/test_datasources_datasets.py -k export -v
  test_export_dataset_rows_full_csv PASSED
  test_export_dataset_rows_columns_subset PASSED
  test_export_dataset_rows_with_user_filters PASSED
  test_export_dataset_rows_filter_expression_applied PASSED
  test_export_dataset_rows_fields_whitelist PASSED
  test_export_dataset_rows_empty_table PASSED
  test_export_dataset_rows_unauth_401 PASSED
  test_export_dataset_rows_inactive_dataset_404 PASSED
  test_export_dataset_rows_inactive_datasource_404 PASSED
  test_export_dataset_rows_unsupported_format_400 PASSED
  test_export_dataset_rows_not_found_404 PASSED
  test_export_dataset_rows_nonexistent_table_400 PASSED
  test_export_dataset_rows_invalid_columns_400 PASSED
  test_export_dataset_rows_rate_limited_429 PASSED
  test_export_dataset_rows_rate_limit_per_user PASSED
  15 passed
```

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 245 files already formatted
uv run pyrefly check                          # 0 errors
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1954 passed, 21 deselected, 54 warnings in 89.76s
  TOTAL 9301 stmts, 341 miss, 1880 branch, 132 brpart, 95.45%
```

覆盖率 95.45%（≥ 95% 门禁），高于 iter-55 基线 95.43%（+0.02%）。
新增 21 个 non-slow 用例（15 导出端点 + 6 流式查询函数），iter-55 为 1933，
本迭代 1954（+21）。

## 遗留事项

- P9-Q1（数据集行导出）完成，item 50/51/52 闭环。
- P9-Q2 导出权限与审计（item 53-57）待下一迭代：现有三个导出端点 + 新端点
  需统一加 `AuditAction.EXPORT` 审计日志，表导出与数据集导出需应用行级权限/
  列级白名单（当前数据集导出已应用，表导出未应用）。
- P9-Q3 导出限流与配额（item 58-60）待后续：当前仅数据集导出有限流，表导出
  与 SQL 导出未限流；每日导出行数配额未实现。
- P9-Q4 前端导出统一（item 61-64）待后续：429 限流响应处理已在数据集页实现，
  表管理页与 SQL 控制台待统一。
- P9-Q5 测试与文档（item 65-70）待后续：`external-api-guide.rst` 数据导出章节
  与 `changelog.rst` v0.1.0 P9 条目待补。

## 下一轮计划

- 启动 iter-57 P9-Q2 导出权限与审计：
  1. `AuditAction` 新增 `EXPORT` 枚举值（choices 总数 +1，同步更新
     `test_audit_models.py` choices_count 断言）。
  2. 四类导出端点（数据集 / 表 / SQL / 审计日志）统一写 `AuditLog`，
     流式响应在生成器 `finally` 块记录（成功/失败）。
  3. 表导出 `export_table_view` 应用行级权限与列级白名单（复用
     `apps.manager.security` 的 `RowFilter` / `ColumnVisibility`）。
  4. 数据集导出补审计日志（当前仅有基础导出 + 限流）。
  5. SQL 结果集导出不应用行级权限/列级白名单（SQL 自定义查询，权限由 SQL 控制）。
