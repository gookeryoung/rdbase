import client from "./client";
import type {
  MessageOut,
  RowCreate,
  RowListResponse,
  RowOut,
  RowQuery,
  RowUpdate,
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
