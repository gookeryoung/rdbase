import client from "./client";
import type {
  Dataset,
  DatasetCreate,
  DatasetList,
  DatasetRows,
  DatasetUpdate,
  MessageOut,
} from "@/types";

// 数据集列表（仅管理员）
export const listDatasets = (): Promise<DatasetList> =>
  client.get<DatasetList>("/datasets").then((res) => res.data);

// 创建数据集（仅管理员）
export const createDataset = (data: DatasetCreate): Promise<Dataset> =>
  client.post<Dataset>("/datasets", data).then((res) => res.data);

// 数据集详情（仅管理员；含 is_active=False 的）
export const retrieveDataset = (slug: string): Promise<Dataset> =>
  client.get<Dataset>(`/datasets/${slug}`).then((res) => res.data);

// 更新数据集（仅管理员，version 自增）
export const updateDataset = (
  slug: string,
  data: DatasetUpdate
): Promise<Dataset> =>
  client.patch<Dataset>(`/datasets/${slug}`, data).then((res) => res.data);

// 删除数据集（仅管理员）
export const deleteDataset = (slug: string): Promise<MessageOut> =>
  client.delete<MessageOut>(`/datasets/${slug}`).then((res) => res.data);

// 预览数据集行（仅管理员，用于诊断/调试）
export const previewDatasetRows = (
  slug: string,
  params: {
    page?: number;
    page_size?: number;
    order_by?: string;
    order_dir?: "asc" | "desc";
    columns?: string;
    filters?: string;
  } = {}
): Promise<DatasetRows> =>
  client
    .get<DatasetRows>(`/datasets/${slug}/preview`, { params })
    .then((res) => res.data);
