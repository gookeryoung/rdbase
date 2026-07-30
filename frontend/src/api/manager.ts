import client from "./client";
import type { RowListResponse, RowQuery } from "@/types";

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
