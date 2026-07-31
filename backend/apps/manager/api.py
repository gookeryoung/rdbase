"""manager 模块 Router - 数据浏览与 CRUD 接口.

P4-1 数据浏览：基于 SQLAlchemy 反射读取目标库表数据。
- 行列表查询：所有登录用户可读。

P4-2 数据 CRUD：单行新增/查询/编辑/删除。
- 写操作（POST/PATCH/DELETE）：designer 或 admin
- 读操作（GET）：所有登录用户
- 主键通过 URL 查询参数 ``pk`` 传递（JSON 字符串，如 ``pk={"id":1}``）
- 乐观锁：UPDATE/DELETE 影响 0 行返回 404 ``行不存在或已被修改``

P4-3 SQL 查询控制台：执行任意 SQL 与获取执行计划。
- POST ``/{ds_id}/query``：SELECT 所有登录用户可执行；DDL/DML 须 designer+
- POST ``/{ds_id}/explain``：所有登录用户可读（EXPLAIN 本身只读）

P4-4 导入导出：CSV/Excel/SQL 脚本导入导出（流式处理大文件）。
- POST ``/{ds_id}/tables/{table_name}/export``：所有登录用户可读
- POST ``/{ds_id}/tables/{table_name}/import``：designer+（写操作）
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse, StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError
from ninja.files import UploadedFile
from sqlalchemy.exc import SQLAlchemyError

from apps.accounts.auth import JWTAuth
from apps.accounts.models import Role
from apps.accounts.permissions import require_designer_or_admin
from apps.datasources.engine import get_engine
from apps.datasources.models import DataSource

from .query import (
    QueryError,
    delete_row,
    execute_sql,
    explain_sql,
    export_excel,
    get_column_names,
    get_row,
    import_rows,
    insert_row,
    iter_table_rows,
    parse_csv_upload,
    parse_excel_upload,
    query_table_rows,
    rows_to_csv,
    rows_to_sql,
    update_row,
)
from .schemas import (
    ExplainIn,
    ExplainOut,
    ImportResultOut,
    MessageOut,
    RowCreateIn,
    RowListOut,
    RowOut,
    RowUpdateIn,
    SqlExecIn,
    SqlResultOut,
)

router = Router(tags=["manager"], auth=JWTAuth())


def _get_ds_or_404(ds_id: int) -> DataSource:
    """按 ID 获取数据源，不存在抛 404."""
    try:
        return DataSource.objects.get(pk=ds_id)
    except DataSource.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, "数据源不存在") from None


def _parse_filters(filters_param: str | None) -> dict[str, dict[str, Any]]:
    """解析 filters JSON 字符串参数为 dict.

    Args:
        filters_param: URL 中的 filters 参数（JSON 字符串），如
            ``'{"name":{"op":"like","val":"J%"}}'``。

    Returns:
        解析后的筛选条件 dict；输入为空时返回空 dict。

    Raises:
        HttpError: JSON 解析失败或结构非法。
    """
    if not filters_param:
        return {}
    try:
        parsed = json.loads(filters_param)
    except json.JSONDecodeError as exc:
        raise HttpError(400, f"filters 参数非法 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise HttpError(400, "filters 须为 JSON 对象")
    return cast("dict[str, dict[str, Any]]", parsed)


def _parse_columns(columns_param: str | None) -> list[str] | None:
    """解析 columns 逗号分隔字符串为列表.

    输入为空时返回 None（表示查询所有列）；非空时按逗号分割并去除空白。
    """
    if not columns_param:
        return None
    cols = [c.strip() for c in columns_param.split(",") if c.strip()]
    return cols or None


def _parse_pk(pk_param: str | None) -> dict[str, Any]:
    """解析 pk JSON 字符串参数为 dict.

    Args:
        pk_param: URL 中的 pk 参数（JSON 字符串），如 ``'{"id":1}'``。

    Returns:
        主键列名 → 值的 dict。

    Raises:
        HttpError: 参数缺失、JSON 解析失败或结构非法。
    """
    if not pk_param:
        raise HttpError(400, "pk 参数不能为空")
    try:
        parsed = json.loads(pk_param)
    except json.JSONDecodeError as exc:
        raise HttpError(400, f"pk 参数非法 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise HttpError(400, "pk 须为 JSON 对象")
    if not parsed:
        raise HttpError(400, "pk 不能为空对象")
    return cast("dict[str, Any]", parsed)


@router.get("/{ds_id}/tables/{table_name}/rows", response={200: RowListOut})
def list_rows_view(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
    page: int = 1,
    page_size: int = 20,
    order_by: str | None = None,
    order_dir: str = "asc",
    columns: str | None = None,
    filters: str | None = None,
) -> HttpResponse:
    """查询表行数据（所有登录用户）.

    Query 参数：
        schema_name: Schema 名（SQLite 自动忽略）。
        page: 页码，从 1 开始，默认 1。
        page_size: 每页行数，默认 20。
        order_by: 排序字段（须为表内列名），默认不排序。
        order_dir: 排序方向，``asc`` 或 ``desc``，默认 ``asc``。
        columns: 逗号分隔的列名列表，控制 SELECT 列；为空时返回所有列。
        filters: JSON 字符串，格式 ``{"列名":{"op":"eq/ne/gt/lt/ge/le/like/in","val":...}}``。
    """
    del request  # 仅认证用
    ds = _get_ds_or_404(ds_id)
    parsed_filters = _parse_filters(filters)
    parsed_columns = _parse_columns(columns)
    effective_schema = schema_name or None
    try:
        engine = get_engine(ds)
        rows, total = query_table_rows(
            engine,
            table_name=table_name,
            schema=effective_schema,
            columns=parsed_columns,
            page=page,
            page_size=page_size,
            order_by=order_by,
            order_dir=order_dir,
            filters=parsed_filters,
        )
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"查询失败: {exc}") from None

    # 实际返回的列名顺序
    returned_columns: list[str]
    if parsed_columns:
        returned_columns = parsed_columns
    elif rows:
        returned_columns = list(rows[0].keys())
    else:
        # 无数据时通过反射获取列名（保证 columns 字段始终非空，便于前端渲染表头）
        try:
            returned_columns = get_column_names(engine, table_name, effective_schema)
        except QueryError:
            returned_columns = []

    body = RowListOut(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        columns=returned_columns,
    ).model_dump()
    return JsonResponse(body)


# ============================================================
# P4-2 行 CRUD
# ============================================================


@router.post("/{ds_id}/tables/{table_name}/rows", response={201: RowOut})
def create_row_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    payload: RowCreateIn,
    schema_name: str | None = None,
) -> HttpResponse:
    """新增单行（designer+）.

    Body: ``{"values": {"col1": val1, "col2": val2}}``。
    返回插入后的完整行（含自增主键回填）。
    """
    require_designer_or_admin(request)
    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    try:
        engine = get_engine(ds)
        row = insert_row(engine, table_name, effective_schema, payload.values)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"新增失败: {exc}") from None
    body = RowOut(row=row).model_dump()
    return JsonResponse(body, status=201)


@router.get("/{ds_id}/tables/{table_name}/rows/pk", response={200: RowOut})
def retrieve_row_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
    pk: str | None = None,
) -> HttpResponse:
    """按主键查单行（所有登录用户）.

    Query 参数：
        schema_name: Schema 名（SQLite 自动忽略）。
        pk: JSON 字符串，主键列名 → 值，如 ``{"id":1}``。
    """
    del request  # 仅认证用
    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    parsed_pk = _parse_pk(pk)
    try:
        engine = get_engine(ds)
        row = get_row(engine, table_name, effective_schema, parsed_pk)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"查询失败: {exc}") from None
    if row is None:
        raise HttpError(404, "行不存在")
    body = RowOut(row=row).model_dump()
    return JsonResponse(body)


@router.patch("/{ds_id}/tables/{table_name}/rows/pk", response={200: RowOut})
def update_row_view(  # noqa: PLR0913, PLR0917
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    payload: RowUpdateIn,
    schema_name: str | None = None,
    pk: str | None = None,
) -> HttpResponse:
    """按主键更新单行（designer+）.

    Body: ``{"values": {"col1": new_val1}}``（不含主键列）。
    Query 参数：
        pk: JSON 字符串，主键列名 → 值，如 ``{"id":1}``。
    返回更新后的完整行。

    乐观锁：行不存在或已被修改时返回 404。
    """
    require_designer_or_admin(request)
    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    parsed_pk = _parse_pk(pk)
    try:
        engine = get_engine(ds)
        row = update_row(engine, table_name, effective_schema, parsed_pk, payload.values)
    except QueryError as exc:
        # 行不存在或已被修改 → 404 区分（消息含「不存在」字样），其他 QueryError → 400
        msg = str(exc)
        if "不存在" in msg:
            raise HttpError(404, msg) from None
        raise HttpError(400, msg) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"更新失败: {exc}") from None
    body = RowOut(row=row).model_dump()
    return JsonResponse(body)


@router.delete("/{ds_id}/tables/{table_name}/rows/pk", response={200: MessageOut})
def delete_row_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
    pk: str | None = None,
) -> HttpResponse:
    """按主键删除单行（designer+）.

    Query 参数：
        pk: JSON 字符串，主键列名 → 值，如 ``{"id":1}``。

    乐观锁：行不存在或已被删除时返回 404。
    """
    require_designer_or_admin(request)
    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    parsed_pk = _parse_pk(pk)
    try:
        engine = get_engine(ds)
        delete_row(engine, table_name, effective_schema, parsed_pk)
    except QueryError as exc:
        msg = str(exc)
        if "不存在" in msg:
            raise HttpError(404, msg) from None
        raise HttpError(400, msg) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"删除失败: {exc}") from None
    return JsonResponse({"detail": "已删除"})


# ============================================================
# P4-3 SQL 查询控制台
# ============================================================


@router.post("/{ds_id}/query", response={200: SqlResultOut})
def execute_sql_view(
    request: HttpRequest,
    ds_id: int,
    payload: SqlExecIn,
) -> HttpResponse:
    """执行任意 SQL（所有登录用户可调，写操作须 designer+）.

    权限分层：

    - viewer 角色：仅允许只读语句（SELECT/WITH/SHOW/DESCRIBE/EXPLAIN），写操作返回 403。
    - designer/admin 角色：允许 SELECT 与 DDL/DML。

    Body: ``{"sql": "SELECT * FROM users"}``。

    返回 ``SqlResultOut``：

    - SELECT 时 ``columns``/``rows`` 为结果集，``rowcount`` 为结果集行数。
    - DDL/DML 时 ``columns``/``rows`` 为空，``rowcount`` 为影响行数（DDL 为 -1）。
    - ``elapsed_ms`` 为执行耗时，``read_only`` 标识实际语句类型。
    """
    user = getattr(request, "auth", None)
    user_role = getattr(user, "role", None)
    # viewer 仅允许只读；designer/admin 允许任意
    read_only = user_role not in (Role.ADMIN, Role.DESIGNER)
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        result = execute_sql(engine, payload.sql, read_only=read_only)
    except QueryError as exc:
        # viewer 越权写操作 → 403；其他 QueryError → 400
        if "仅允许执行只读" in str(exc):
            raise HttpError(403, str(exc)) from None
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"SQL 执行失败: {exc}") from None
    body = SqlResultOut(
        columns=result["columns"],
        rows=result["rows"],
        rowcount=result["rowcount"],
        elapsed_ms=result["elapsed_ms"],
        read_only=result["read_only"],
    ).model_dump()
    return JsonResponse(body)


@router.post("/{ds_id}/explain", response={200: ExplainOut})
def explain_sql_view(
    request: HttpRequest,
    ds_id: int,
    payload: ExplainIn,
) -> HttpResponse:
    """获取 SQL 执行计划（所有登录用户可读）.

    EXPLAIN 本身只读，所有登录用户均可调用。多方言适配：

    - SQLite: ``EXPLAIN QUERY PLAN <sql>``（``analyze`` 参数忽略）
    - PostgreSQL/MySQL: ``EXPLAIN [ANALYZE] <sql>``

    Body: ``{"sql": "SELECT * FROM users", "analyze": false}``。
    """
    del request  # 仅认证用
    ds = _get_ds_or_404(ds_id)
    try:
        engine = get_engine(ds)
        result = explain_sql(engine, payload.sql, analyze=payload.analyze)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"EXPLAIN 执行失败: {exc}") from None
    body = ExplainOut(
        plan=result["plan"],
        rows=result["rows"],
        columns=result["columns"],
        analyze=result["analyze"],
        dialect=result["dialect"],
    ).model_dump()
    return JsonResponse(body)


# ============================================================
# P4-4 导入导出
# ============================================================

# 支持的导出格式 → (扩展名, content_type)
_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "sql": ("sql", "application/sql; charset=utf-8"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


@router.post("/{ds_id}/tables/{table_name}/export")
def export_table_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
    format: str = "csv",
) -> HttpResponseBase:
    """导出表数据为 CSV/Excel/SQL 脚本（所有登录用户可读）.

    Query 参数：
        schema_name: Schema 名（SQLite 自动忽略）。
        format: 导出格式，``csv``（默认）/``xlsx``/``sql``。

    返回文件下载响应：

    - ``csv``: ``StreamingHttpResponse``，``text/csv; charset=utf-8``，含 UTF-8 BOM。
    - ``sql``: ``StreamingHttpResponse``，``application/sql; charset=utf-8``，每行一条 INSERT。
    - ``xlsx``: ``HttpResponse``，``application/vnd.openxmlformats-officedocument.spreadsheetml.sheet``。

    流式响应：CSV/SQL 用生成器分块产出，避免大表 OOM；Excel 用 ``write_only`` 模式逐行写入。
    """
    del request  # 仅认证用
    fmt = format.lower()
    if fmt not in _EXPORT_FORMATS:
        raise HttpError(400, f"不支持的导出格式: {format}（可选 csv/xlsx/sql）")
    ext, content_type = _EXPORT_FORMATS[fmt]

    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    try:
        engine = get_engine(ds)
        columns = get_column_names(engine, table_name, effective_schema)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"读取表元数据失败: {exc}") from None

    filename = f"{table_name}.{ext}"
    # 文件名含中文时用 RFC 5987 编码，避免浏览器乱码
    from urllib.parse import quote

    disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"

    if fmt == "xlsx":
        try:
            data = export_excel(engine, table_name, effective_schema)
        except QueryError as exc:
            raise HttpError(400, str(exc)) from None
        except SQLAlchemyError as exc:
            raise HttpError(400, f"导出失败: {exc}") from None
        resp = HttpResponse(data, content_type=content_type)
        resp["Content-Disposition"] = disposition
        return resp

    # CSV / SQL：流式生成器
    try:
        rows_iter = iter_table_rows(engine, table_name, effective_schema)
        if fmt == "csv":
            chunks: Iterator[str] = rows_to_csv(rows_iter, columns)
        else:
            dialect = engine.dialect.name
            chunks = rows_to_sql(rows_iter, columns, table_name, effective_schema, dialect)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"导出失败: {exc}") from None

    # 流式响应：用生成器逐块产出（SQLAlchemy 连接在生成器消费时维持打开）
    def _stream() -> Iterator[bytes]:
        try:
            for chunk in chunks:
                yield chunk.encode("utf-8")
        except QueryError as exc:
            # 生成器消费阶段抛错：响应头已发送，无法改状态码，写入错误信息到响应体
            yield f"\n[导出错误] {exc}".encode()
        except SQLAlchemyError as exc:
            yield f"\n[导出错误] {exc}".encode()

    resp = StreamingHttpResponse(_stream(), content_type=content_type)
    resp["Content-Disposition"] = disposition
    return resp


# 导入文件扩展名 → 解析器
_IMPORT_PARSERS = {
    ".csv": parse_csv_upload,
    ".xlsx": parse_excel_upload,
}


@router.post("/{ds_id}/tables/{table_name}/import", response={200: ImportResultOut})
def import_table_view(
    request: HttpRequest,
    ds_id: int,
    table_name: str,
    schema_name: str | None = None,
) -> HttpResponse:
    """导入 CSV/Excel 文件到指定表（designer+，事务批量插入）.

    Query 参数：
        schema_name: Schema 名（SQLite 自动忽略）。

    Body: ``multipart/form-data``，字段 ``file`` 为上传的 ``.csv`` 或 ``.xlsx`` 文件。

    返回 :class:`ImportResultOut`：

    - 成功：``success_count`` 为插入行数，``failed_count`` 为 0，``errors`` 为空。
    - 失败：抛 400 ``导入失败: <原因>``（事务回滚，无部分插入）。
    """
    require_designer_or_admin(request)
    file = cast("UploadedFile | None", request.FILES.get("file"))
    if file is None:
        raise HttpError(400, "file 参数不能为空")
    # UploadedFile.name 含扩展名，用于选择解析器
    ext = _get_file_ext(file.name)
    parser = _IMPORT_PARSERS.get(ext)
    if parser is None:
        raise HttpError(400, f"不支持的文件类型: {file.name}（仅支持 .csv/.xlsx）")

    ds = _get_ds_or_404(ds_id)
    effective_schema = schema_name or None
    try:
        engine = get_engine(ds)
        columns, rows_iter = parser(file)
        result = import_rows(engine, table_name, effective_schema, columns, rows_iter)
    except QueryError as exc:
        raise HttpError(400, str(exc)) from None
    except SQLAlchemyError as exc:
        raise HttpError(400, f"导入失败: {exc}") from None
    body = ImportResultOut(
        success_count=result["success_count"],
        failed_count=result["failed_count"],
        errors=result["errors"],
    ).model_dump()
    return JsonResponse(body)


def _get_file_ext(name: str | None) -> str:
    """从文件名提取小写扩展名（含点）."""
    if not name:
        return ""
    dot = name.rfind(".")
    if dot < 0:
        return ""
    return name[dot:].lower()


__all__ = ["router"]
