import client from "./client";
import type {
  AuditLog,
  AuditLogList,
  AuditLogQuery,
} from "@/types";

// 构造查询参数字符串（仅包含非空字段）
const buildQuery = (q: AuditLogQuery): Record<string, string> => {
  const params: Record<string, string> = {};
  if (q.user_id !== undefined && q.user_id !== null) params.user_id = String(q.user_id);
  if (q.username) params.username = q.username;
  if (q.action) params.action = q.action;
  if (q.source) params.source = q.source;
  if (q.status) params.status = q.status;
  if (q.resource_type) params.resource_type = q.resource_type;
  if (q.datasource_id !== undefined && q.datasource_id !== null)
    params.datasource_id = String(q.datasource_id);
  if (q.path) params.path = q.path;
  if (q.start) params.start = q.start;
  if (q.end) params.end = q.end;
  if (q.page !== undefined) params.page = String(q.page);
  if (q.page_size !== undefined) params.page_size = String(q.page_size);
  return params;
};

// 分页查询审计日志（仅管理员）
export const listAuditLogs = (q: AuditLogQuery = {}): Promise<AuditLogList> =>
  client
    .get<AuditLogList>("/audit/logs", { params: buildQuery(q) })
    .then((res) => res.data);

// 获取单条审计日志详情（仅管理员）
export const retrieveAuditLog = (id: number): Promise<AuditLog> =>
  client.get<AuditLog>(`/audit/logs/${id}`).then((res) => res.data);

// 导出审计日志为 CSV（流式下载，仅管理员）
// 传入筛选条件，返回 Blob 供前端触发下载
export const exportAuditLogs = async (q: AuditLogQuery = {}): Promise<void> => {
  const res = await client.get("/audit/logs/export", {
    params: buildQuery(q),
    responseType: "blob",
  });
  // 从 Content-Disposition 解析文件名，失败回退默认名
  const disposition = res.headers?.["content-disposition"] ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? "audit_logs.csv";
  const blob = new Blob([res.data as BlobPart], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
