import client from "./client";
import type {
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
