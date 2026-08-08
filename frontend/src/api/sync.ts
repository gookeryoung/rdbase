import client from "./client";
import type {
  SyncAlert,
  SyncAlertList,
  SyncBatchRequest,
  SyncBatchResult,
  SyncConfig,
  SyncConfigCreate,
  SyncConfigList,
  SyncConfigUpdate,
  SyncLogList,
  SyncPreview,
  SyncResult,
  SyncScheduleUpdate,
  SyncStats,
} from "@/types";

// 列出所有同步配置
export const listSyncConfigs = (): Promise<SyncConfigList> =>
  client.get<SyncConfigList>("/sync/configs").then((res) => res.data);

// 创建同步配置
export const createSyncConfig = (data: SyncConfigCreate): Promise<SyncConfig> =>
  client.post<SyncConfig>("/sync/configs", data).then((res) => res.data);

// 获取单个同步配置
export const retrieveSyncConfig = (id: number): Promise<SyncConfig> =>
  client.get<SyncConfig>(`/sync/configs/${id}`).then((res) => res.data);

// 更新同步配置
export const updateSyncConfig = (
  id: number,
  data: SyncConfigUpdate
): Promise<SyncConfig> =>
  client.patch<SyncConfig>(`/sync/configs/${id}`, data).then((res) => res.data);

// 删除同步配置
export const deleteSyncConfig = (id: number): Promise<{ detail: string }> =>
  client.delete<{ detail: string }>(`/sync/configs/${id}`).then((res) => res.data);

// 触发同步
export const triggerSync = (
  id: number,
  payload: { confirm: boolean; force_full?: boolean }
): Promise<SyncResult> =>
  client
    .post<SyncResult>(`/sync/configs/${id}/trigger`, payload)
    .then((res) => res.data);

// 预览同步数据
export const previewSync = (
  id: number,
  payload: { force_full?: boolean }
): Promise<SyncPreview> =>
  client
    .post<SyncPreview>(`/sync/configs/${id}/preview`, payload)
    .then((res) => res.data);

// 批量触发同步
export const batchTriggerSync = (
  payload: SyncBatchRequest
): Promise<SyncBatchResult> =>
  client.post<SyncBatchResult>("/sync/batch-trigger", payload).then((res) => res.data);

// 执行定时同步
export const runScheduledSync = (): Promise<SyncBatchResult> =>
  client.post<SyncBatchResult>("/sync/scheduled", {}).then((res) => res.data);

// 更新调度设置
export const updateSchedule = (
  id: number,
  payload: SyncScheduleUpdate
): Promise<SyncConfig> =>
  client
    .post<SyncConfig>(`/sync/configs/${id}/schedule`, payload)
    .then((res) => res.data);

// 列出同步日志
export const listSyncLogs = (
  configId?: number,
  limit = 50
): Promise<SyncLogList> => {
  const params: Record<string, unknown> = { limit };
  if (configId !== undefined) params.config_id = configId;
  return client
    .get<SyncLogList>("/sync/logs", { params })
    .then((res) => res.data);
};

// 获取同步统计（成功率、平均耗时、总读写行数）
export const getSyncStats = (
  configId?: number,
  days?: number
): Promise<SyncStats> => {
  const params: Record<string, unknown> = {};
  if (configId !== undefined) params.config_id = configId;
  if (days !== undefined) params.days = days;
  return client
    .get<SyncStats>("/sync/stats", { params })
    .then((res) => res.data);
};

// 列出同步告警
export const listSyncAlerts = (
  params: {
    configId?: number;
    acknowledged?: boolean;
    level?: string;
    limit?: number;
  } = {}
): Promise<SyncAlertList> => {
  const query: Record<string, unknown> = { limit: params.limit ?? 50 };
  if (params.configId !== undefined) query.config_id = params.configId;
  if (params.acknowledged !== undefined) query.acknowledged = params.acknowledged;
  if (params.level !== undefined) query.level = params.level;
  return client
    .get<SyncAlertList>("/sync/alerts", { params: query })
    .then((res) => res.data);
};

// 确认告警（标记已处理）
export const ackSyncAlert = (id: number): Promise<SyncAlert> =>
  client.post<SyncAlert>(`/sync/alerts/${id}/ack`, {}).then((res) => res.data);
