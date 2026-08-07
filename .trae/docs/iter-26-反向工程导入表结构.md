# iter-26 反向工程导入表结构

## 需求清单

- [x] 数据库设计页（Designer.tsx）新建草稿时支持从已载入数据源的已有表反向工程导入结构
- [x] 导入的 spec 作为草稿初始结构，编辑后应用回库时按 ALTER 处理

## 迭代目标

用户原话：「数据库设计需要能够显示已经载入的数据库的表，能够编辑修改之后再应用回去。」

在新建草稿 Modal 中新增"从已有表导入结构"按钮：用户填好数据源 + 表名（可选 schema）后点击按钮，调用新增的反向工程接口把反射的表结构以 `TableDesignSpec` 格式返回并填充到草稿初始 spec。后续用户在表设计器中编辑该 spec（增删字段/索引/外键），点击"应用"时后端 `_resolve_old_spec` 自动反射当前表结构作为 `old_spec`，按 ALTER 生成 DDL 应用回库。

## 改动文件清单

### 后端

- [backend/apps/designer/api.py](file:///f:/Dev/rdbase/backend/apps/designer/api.py)：新增 `reverse_table_view`，`GET /{ds_id}/tables/{table_name}/reverse`，query `schema_name`，返回 `TableDesignSpec`。复用 `_reflect_table_to_spec` 的方言规整逻辑。

### 前端

- [frontend/src/api/designer.ts](file:///f:/Dev/rdbase/frontend/src/api/designer.ts)：新增 `reverseTable(dsId, tableName, schemaName?)` API，import `TableDesignSpec` 类型。
- [frontend/src/pages/Designer.tsx](file:///f:/Dev/rdbase/frontend/src/pages/Designer.tsx)：
  - `DraftFormValues` 新增 `imported_spec?: TableDesignSpec | null` 字段
  - 新增 `importing` loading 状态、`handleImportFromTable` 处理函数
  - `openCreate` / 初始 state 同步初始化 `imported_spec: null`
  - `handleCreate` 优先使用 `imported_spec`，未导入时回退到默认空 spec（仅含 id 字段）
  - Modal 新增"从已有表导入结构"按钮、导入成功 Tag 提示、说明文字
  - 表名/schema 输入框 onChange 在值变化时自动清空 `imported_spec`（避免 spec 与表名不一致导致 ALTER 误判）
  - 新增 `ImportOutlined` 图标 import

### 测试

- [tests/test_designer_api.py](file:///f:/Dev/rdbase/tests/test_designer_api.py)：新增 4 个 reverse 接口测试
  - `test_reverse_table_returns_design_spec`：viewer 可读，返回完整 spec（字段/主键非空/SQLite 隐式自增/default None/外键 on_delete=RESTRICT/索引保留）
  - `test_reverse_table_unique_index_preserved`：显式唯一索引 `unique=True` 保留
  - `test_reverse_table_not_found_returns_404`：表不存在返回 404
  - `test_reverse_table_unknown_ds_returns_404`：数据源不存在返回 404

## 关键决策与依据

1. **复用 `_reflect_table_to_spec` 而非新写反射逻辑**：该函数原本服务于 `_resolve_old_spec`（自动 ALTER 时反射 old_spec），已包含方言规整（主键强制非空、SQLite INTEGER PK 隐式自增、自增主键 default 置 None、外键 on_delete 统一 RESTRICT），保证导入草稿后立即应用回库不会因表示差异误判触发无意义 ALTER。反向工程接口与 ALTER 比较共用同一规整逻辑，语义一致。
2. **接口路径 `/tables/{table_name}/reverse`**：挂在 `retrieve_table_view`（`/tables/{table_name}`）之后，django-ninja 路由按最长匹配，`/reverse` 子路径不会与 `/tables/{table_name}` 冲突。
3. **权限：所有登录用户可读**：与 `retrieve_table_view` 一致——反向工程是只读反射操作，viewer/designer/admin 均可调用。写入（创建草稿、应用 DDL）的权限检查在 `create_draft_view` / `apply_draft_view` 中独立控制。
4. **前端 UX：表名/schema 变化时清空 imported_spec**：避免用户导入 "users" 表结构后把表名改成 "orders" 导致草稿 spec.name（仍为 "users"）与 draft.table_name（"orders"）不一致——后端 `_resolve_old_spec` 按 `spec.name` 反射 old_spec，会去 ALTER "users" 而非 "orders"。表名/schema 任一变化时清空 imported_spec 强制重新导入。
5. **未自动同步 spec.name 到表名输入框**：导入按钮要求用户先填好表名再点击，导入成功后表名通常与 spec.name 一致（数据库一般原样返回）。若不一致，用户改表名会触发清空逻辑，再次提示重新导入，避免不一致状态进入草稿。
6. **不修改 createDraft API**：`createDraft` 已接受 `spec` 参数，前端只需把导入的 spec 替代默认空 spec 传入即可，无需后端 createDraft 改动。

## 代码实现情况

### 后端 reverse_table_view

```python
@router.get("/{ds_id}/tables/{table_name}/reverse", response={200: TableDesignSpec})
def reverse_table_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
) -> HttpResponse:
    """反向工程：把已有表结构反射为 TableDesignSpec，供新建草稿导入（所有登录用户）."""
    del request
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        effective_schema = schema_name if schema_name else None
        meta = inspect_table(engine, table_name, schema=effective_schema)
        spec = _reflect_table_to_spec(meta, cast(str, ds.engine))
    except (SQLAlchemyError, NoSuchTableError) as exc:
        raise _wrap_reflect_error(exc) from None
    body = spec.model_dump()
    return JsonResponse(body)
```

### 前端 handleImportFromTable

```tsx
const handleImportFromTable = async () => {
  if (!createForm.datasource_id || !createForm.table_name) {
    message.warning("请先选择数据源并填写表名");
    return;
  }
  setImporting(true);
  try {
    const spec = await reverseTable(
      createForm.datasource_id,
      createForm.table_name,
      createForm.schema_name || null
    );
    setCreateForm((f) => ({ ...f, imported_spec: spec }));
    message.success(
      `已导入表结构：${spec.fields.length} 个字段、${spec.indexes.length} 个索引、${spec.foreign_keys.length} 个外键`
    );
  } catch (err) {
    message.error(errMsg(err, "导入失败，请确认表名与 schema 是否正确"));
  } finally {
    setImporting(false);
  }
};
```

### handleCreate 优先使用 imported_spec

```tsx
const spec: TableDesignSpec =
  createForm.imported_spec ?? {
    name: createForm.table_name,
    schema_name: createForm.schema_name || null,
    comment: null,
    fields: [makeDefaultIdField()],
    indexes: [],
    foreign_keys: [],
  };
```

## 整合优化情况

- 反向工程接口与既有 `_resolve_old_spec`、`preview_ddl_view`、`apply_draft_view` 共用 `_reflect_table_to_spec`，方言规整逻辑统一在一处，避免散落多处导致规整规则不一致而误判 ALTER。
- 前端 `reverseTable` API 与 `retrieveTable` 风格一致（同样的 dsId/tableName/schemaName 签名与 params 处理），便于维护。

## 测试验证结果

- 前端 `npx tsc --noEmit`：通过
- 后端 `make check`：
  - `ruff check` / `ruff format --check`：通过
  - `pyrefly check`：0 errors
  - `pytest -m "not slow"`：914 passed，覆盖率 97.82%（>=95%）
- 新增 4 个 reverse 接口测试全部通过，覆盖 viewer 权限、字段规整、外键/索引保留、404 分支。

## 遗留事项

无。后端 ALTER 应用回库的能力在 iter-21 已完成（`_resolve_old_spec` + 自动 ALTER DDL 生成），本次反向工程接口与之无缝衔接。

## 下一轮计划

无明确下一轮需求。等待用户提出新需求或反馈。
