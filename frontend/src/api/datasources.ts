import client from "./client";
import type {
  DataSource,
  DataSourceCreate,
  DataSourceUpdate,
  TestConnection,
  TestConnectionResult,
} from "@/types";

// 数据源列表（所有登录用户可访问）
export const listDatasources = (): Promise<DataSource[]> =>
  client.get<DataSource[]>("/datasources").then((res) => res.data);

// 创建数据源（仅 admin）
export const createDatasource = (data: DataSourceCreate): Promise<DataSource> =>
  client.post<DataSource>("/datasources", data).then((res) => res.data);

// 数据源详情
export const retrieveDatasource = (id: number): Promise<DataSource> =>
  client.get<DataSource>(`/datasources/${id}`).then((res) => res.data);

// 更新数据源（仅 admin，所有字段可选）
export const updateDatasource = (
  id: number,
  data: DataSourceUpdate
): Promise<DataSource> =>
  client.patch<DataSource>(`/datasources/${id}`, data).then((res) => res.data);

// 删除数据源（仅 admin）
export const deleteDatasource = (id: number): Promise<void> =>
  client.delete(`/datasources/${id}`).then(() => undefined);

// 测试已保存数据源连接（所有登录用户）
export const testSavedDatasource = (id: number): Promise<TestConnectionResult> =>
  client
    .post<TestConnectionResult>(`/datasources/${id}/test`)
    .then((res) => res.data);

// 测试临时连接配置（仅 admin）
export const testTempConnection = (
  data: TestConnection
): Promise<TestConnectionResult> =>
  client
    .post<TestConnectionResult>("/datasources/test", data)
    .then((res) => res.data);
