import client from "./client";
import type {
  ExplainRequest,
  ExplainResult,
  ExportFormat,
  ImportResult,
  MessageOut,
  NameItem,
  ObjectUpdate,
  RoutineBrief,
  RoutineDetail,
  RoutineKind,
  RowCreate,
  RowListResponse,
  RowOut,
  RowQuery,
  RowUpdate,
  SqlExecRequest,
  SqlResult,
  TriggerBrief,
  TriggerDetail,
  ViewDetail,
} from "@/types";

// 主键序列化为 JSON 字符串并 URL 编码（用于 query 参数）
const encodePk = (pk: Record<string, unknown>): string =>
  encodeURIComponent(JSON.stringify(pk));

// 查询表行数据（所有登录用户可读）
// filters 通过 JSON 字符串参数传递，columns 通过逗号分隔字符串传递
export const listRows = (
  dsId: number,
  tableName: string,
  params?: RowQuery
): Promise<RowListResponse> => {
  const query: Record<string, string | number> = {};
  if (params?.schema_name) query.schema_name = params.schema_name;
  if (params?.page != null) query.page = params.page;
  if (params?.page_size != null) query.page_size = params.page_size;
  if (params?.order_by) query.order_by = params.order_by;
  if (params?.order_dir) query.order_dir = params.order_dir;
  if (params?.columns && params.columns.length > 0) {
    query.columns = params.columns.join(",");
  }
  if (params?.filters && Object.keys(params.filters).length > 0) {
    query.filters = JSON.stringify(params.filters);
  }
  return client
    .get<RowListResponse>(`/manager/${dsId}/tables/${tableName}/rows`, {
      params: query,
    })
    .then((res) => res.data);
};

// 新增单行（designer+），返回插入后的完整行（含自增主键回填）
export const createRow = (
  dsId: number,
  tableName: string,
  body: RowCreate,
  schemaName?: string | null
): Promise<RowOut> => {
  const query: Record<string, string> = {};
  if (schemaName) query.schema_name = schemaName;
  return client
    .post<RowOut>(`/manager/${dsId}/tables/${tableName}/rows`, body, {
      params: query,
    })
    .then((res) => res.data);
};

// 按主键查单行（所有登录用户可读）
export const retrieveRow = (
  dsId: number,
  tableName: string,
  pk: Record<string, unknown>,
  schemaName?: string | null
): Promise<RowOut> => {
  const query: Record<string, string> = { pk: encodePk(pk) };
  if (schemaName) query.schema_name = schemaName;
  return client
    .get<RowOut>(`/manager/${dsId}/tables/${tableName}/rows/pk`, {
      params: query,
    })
    .then((res) => res.data);
};

// 按主键更新单行（designer+），返回更新后的完整行
export const updateRow = (
  dsId: number,
  tableName: string,
  pk: Record<string, unknown>,
  body: RowUpdate,
  schemaName?: string | null
): Promise<RowOut> => {
  const query: Record<string, string> = { pk: encodePk(pk) };
  if (schemaName) query.schema_name = schemaName;
  return client
    .patch<RowOut>(`/manager/${dsId}/tables/${tableName}/rows/pk`, body, {
      params: query,
    })
    .then((res) => res.data);
};

// 按主键删除单行（designer+），返回消息
export const deleteRow = (
  dsId: number,
  tableName: string,
  pk: Record<string, unknown>,
  schemaName?: string | null
): Promise<MessageOut> => {
  const query: Record<string, string> = { pk: encodePk(pk) };
  if (schemaName) query.schema_name = schemaName;
  return client
    .delete<MessageOut>(`/manager/${dsId}/tables/${tableName}/rows/pk`, {
      params: query,
    })
    .then((res) => res.data);
};

// ----------------- SQL 查询控制台（P4-3） -----------------

// 执行任意 SQL（viewer 仅 SELECT；designer+ 可执行 DDL/DML）
export const executeSql = (
  dsId: number,
  body: SqlExecRequest
): Promise<SqlResult> =>
  client
    .post<SqlResult>(`/manager/${dsId}/query`, body)
    .then((res) => res.data);

// 获取 SQL 执行计划（所有登录用户可读）
export const explainSql = (
  dsId: number,
  body: ExplainRequest
): Promise<ExplainResult> =>
  client
    .post<ExplainResult>(`/manager/${dsId}/explain`, body)
    .then((res) => res.data);

// ----------------- 导入导出（P4-4） -----------------

// 导出表数据（所有登录用户可读）
// 以 Blob 返回，前端可触发下载；format: csv/xlsx/sql
export const exportTable = (
  dsId: number,
  tableName: string,
  format: ExportFormat,
  schemaName?: string | null
): Promise<Blob> => {
  const query: Record<string, string> = { format };
  if (schemaName) query.schema_name = schemaName;
  return client
    .post<Blob>(`/manager/${dsId}/tables/${tableName}/export`, undefined, {
      params: query,
      responseType: "blob",
    })
    .then((res) => res.data);
};

// 导入 CSV/Excel 文件到指定表（designer+，事务批量插入）
export const importTable = (
  dsId: number,
  tableName: string,
  file: File,
  schemaName?: string | null
): Promise<ImportResult> => {
  const form = new FormData();
  form.append("file", file);
  const query: Record<string, string> = {};
  if (schemaName) query.schema_name = schemaName;
  return client
    .post<ImportResult>(`/manager/${dsId}/tables/${tableName}/import`, form, {
      params: query,
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((res) => res.data);
};

// ----------------- 对象管理（P4-5） -----------------

// 通用 query 参数构造
const buildSchemaQuery = (schemaName?: string | null): Record<string, string> => {
  const q: Record<string, string> = {};
  if (schemaName) q.schema_name = schemaName;
  return q;
};

// 列出视图（所有登录用户）
export const listViews = (
  dsId: number,
  schemaName?: string | null
): Promise<NameItem[]> =>
  client
    .get<NameItem[]>(`/manager/${dsId}/views`, { params: buildSchemaQuery(schemaName) })
    .then((res) => res.data);

// 获取视图定义（所有登录用户）
export const retrieveView = (
  dsId: number,
  name: string,
  schemaName?: string | null
): Promise<ViewDetail> =>
  client
    .get<ViewDetail>(`/manager/${dsId}/views/${encodeURIComponent(name)}`, {
      params: buildSchemaQuery(schemaName),
    })
    .then((res) => res.data);

// 编辑视图（designer+，DROP IF EXISTS + CREATE 事务）
export const updateView = (
  dsId: number,
  name: string,
  body: ObjectUpdate,
  schemaName?: string | null
): Promise<ViewDetail> =>
  client
    .put<ViewDetail>(
      `/manager/${dsId}/views/${encodeURIComponent(name)}`,
      body,
      { params: buildSchemaQuery(schemaName) }
    )
    .then((res) => res.data);

// 删除视图（designer+）
export const deleteView = (
  dsId: number,
  name: string,
  schemaName?: string | null
): Promise<MessageOut> =>
  client
    .delete<MessageOut>(`/manager/${dsId}/views/${encodeURIComponent(name)}`, {
      params: buildSchemaQuery(schemaName),
    })
    .then((res) => res.data);

// 列出存储过程与函数（所有登录用户；SQLite 返回空列表）
export const listRoutines = (
  dsId: number,
  schemaName?: string | null
): Promise<RoutineBrief[]> =>
  client
    .get<RoutineBrief[]>(`/manager/${dsId}/routines`, {
      params: buildSchemaQuery(schemaName),
    })
    .then((res) => res.data);

// 获取存储过程/函数定义（所有登录用户）
// type: procedure 或 function，默认 function
export const retrieveRoutine = (
  dsId: number,
  name: string,
  type: RoutineKind = "function",
  schemaName?: string | null
): Promise<RoutineDetail> => {
  const q = { ...buildSchemaQuery(schemaName), type };
  return client
    .get<RoutineDetail>(`/manager/${dsId}/routines/${encodeURIComponent(name)}`, { params: q })
    .then((res) => res.data);
};

// 编辑存储过程/函数（designer+）
export const updateRoutine = (
  dsId: number,
  name: string,
  body: ObjectUpdate,
  type: RoutineKind = "function",
  schemaName?: string | null
): Promise<RoutineDetail> => {
  const q = { ...buildSchemaQuery(schemaName), type };
  return client
    .put<RoutineDetail>(
      `/manager/${dsId}/routines/${encodeURIComponent(name)}`,
      body,
      { params: q }
    )
    .then((res) => res.data);
};

// 删除存储过程/函数（designer+）
export const deleteRoutine = (
  dsId: number,
  name: string,
  type: RoutineKind = "function",
  schemaName?: string | null
): Promise<MessageOut> => {
  const q = { ...buildSchemaQuery(schemaName), type };
  return client
    .delete<MessageOut>(`/manager/${dsId}/routines/${encodeURIComponent(name)}`, { params: q })
    .then((res) => res.data);
};

// 列出触发器（所有登录用户）
export const listTriggers = (
  dsId: number,
  schemaName?: string | null
): Promise<TriggerBrief[]> =>
  client
    .get<TriggerBrief[]>(`/manager/${dsId}/triggers`, {
      params: buildSchemaQuery(schemaName),
    })
    .then((res) => res.data);

// 获取触发器定义（所有登录用户）
export const retrieveTrigger = (
  dsId: number,
  name: string,
  schemaName?: string | null
): Promise<TriggerDetail> =>
  client
    .get<TriggerDetail>(`/manager/${dsId}/triggers/${encodeURIComponent(name)}`, {
      params: buildSchemaQuery(schemaName),
    })
    .then((res) => res.data);

// 编辑触发器（designer+，PG 需 table 字段）
export const updateTrigger = (
  dsId: number,
  name: string,
  body: ObjectUpdate,
  schemaName?: string | null
): Promise<TriggerDetail> =>
  client
    .put<TriggerDetail>(
      `/manager/${dsId}/triggers/${encodeURIComponent(name)}`,
      body,
      { params: buildSchemaQuery(schemaName) }
    )
    .then((res) => res.data);

// 删除触发器（designer+，PG 需 table 字段）
export const deleteTrigger = (
  dsId: number,
  name: string,
  schemaName?: string | null,
  table?: string | null
): Promise<MessageOut> => {
  const q = buildSchemaQuery(schemaName);
  if (table) q.table = table;
  return client
    .delete<MessageOut>(`/manager/${dsId}/triggers/${encodeURIComponent(name)}`, { params: q })
    .then((res) => res.data);
};
