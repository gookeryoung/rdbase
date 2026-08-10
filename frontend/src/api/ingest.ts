import client from "./client";
import type {
  IngestAlert,
  IngestLog,
  IngestQualityReport,
  IngestQualitySummary,
  IngestRunResult,
  IngestStats,
  IngestTask,
  IngestTaskCreate,
  IngestTaskUpdate,
} from "@/types";

// 列出全部爬取任务（后端返回数组，非 {items} 包装）
export const listIngestTasks = (): Promise<IngestTask[]> =>
  client.get<IngestTask[]>("/ingest/tasks").then((res) => res.data);

// 创建爬取任务
export const createIngestTask = (data: IngestTaskCreate): Promise<IngestTask> =>
  client.post<IngestTask>("/ingest/tasks", data).then((res) => res.data);

// 获取爬取任务详情
export const retrieveIngestTask = (id: number): Promise<IngestTask> =>
  client.get<IngestTask>(`/ingest/tasks/${id}`).then((res) => res.data);

// 更新爬取任务（全量更新）
export const updateIngestTask = (id: number, data: IngestTaskUpdate): Promise<IngestTask> =>
  client.put<IngestTask>(`/ingest/tasks/${id}`, data).then((res) => res.data);

// 删除爬取任务
export const deleteIngestTask = (id: number): Promise<{ message: string }> =>
  client.delete<{ message: string }>(`/ingest/tasks/${id}`).then((res) => res.data);

// 手动触发爬取任务执行（子进程运行 Scrapy，同步返回）
export const runIngestTask = (id: number): Promise<IngestRunResult> =>
  client.post<IngestRunResult>(`/ingest/tasks/${id}/run`).then((res) => res.data);

// 列出指定任务的执行日志
export const listIngestTaskLogs = (taskId: number): Promise<IngestLog[]> =>
  client.get<IngestLog[]>(`/ingest/tasks/${taskId}/logs`).then((res) => res.data);

// 列出爬取告警（默认仅未确认，all=true 返回全部）
export const listIngestAlerts = (all = false): Promise<IngestAlert[]> => {
  const params: Record<string, unknown> = {};
  if (all) params.all = "true";
  return client
    .get<IngestAlert[]>("/ingest/alerts", { params })
    .then((res) => res.data);
};

// 确认爬取告警
export const ackIngestAlert = (id: number): Promise<IngestAlert> =>
  client.post<IngestAlert>(`/ingest/alerts/${id}/ack`).then((res) => res.data);

// 获取爬取统计（可选 ?days=N 限定最近天数）
export const getIngestStats = (days?: number): Promise<IngestStats> => {
  const params: Record<string, unknown> = {};
  if (days !== undefined) params.days = days;
  return client
    .get<IngestStats>("/ingest/stats", { params })
    .then((res) => res.data);
};

// 列出指定任务的数据质量报告（可选 ?log_id=N 限定某次执行）
export const listIngestQualityReports = (
  taskId: number,
  logId?: number,
): Promise<IngestQualityReport[]> => {
  const params: Record<string, unknown> = {};
  if (logId !== undefined) params.log_id = logId;
  return client
    .get<IngestQualityReport[]>(`/ingest/tasks/${taskId}/quality-reports`, { params })
    .then((res) => res.data);
};

// 获取指定任务最近一批质量报告的汇总摘要
export const getIngestQualitySummary = (
  taskId: number,
): Promise<IngestQualitySummary> =>
  client
    .get<IngestQualitySummary>(`/ingest/tasks/${taskId}/quality-summary`)
    .then((res) => res.data);
