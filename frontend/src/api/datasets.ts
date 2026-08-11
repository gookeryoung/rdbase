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

// 导出数据集行为 CSV（所有登录用户可读，流式下载）
// 触发浏览器下载，返回 void；下载中由调用方维护 loading 状态
export const exportDatasetCsv = async (
  slug: string,
  params: { columns?: string; filters?: string } = {}
): Promise<void> => {
  const res = await client.get(`/datasets/${slug}/export`, {
    params,
    responseType: "blob",
  });
  // 从 Content-Disposition 解析文件名，失败回退默认名
  const disposition = res.headers?.["content-disposition"] ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? `${slug}.csv`;
  const blob = new Blob([res.data as BlobPart], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
