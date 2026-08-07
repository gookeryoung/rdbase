import client from "./client";
import type {
  DDLExecuteRequest,
  DDLPreviewRequest,
  DDLResult,
  Draft,
  DraftCreate,
  DraftUpdate,
  NameItem,
  TableBrief,
  TableDesignSpec,
  TableDetail,
  Version,
} from "@/types";

// ----- 元数据反射（所有登录用户可读）-----

// 列出数据源服务器上的所有数据库
export const listDatabases = (dsId: number): Promise<NameItem[]> =>
  client.get<NameItem[]>(`/designer/${dsId}/databases`).then((res) => res.data);

// 列出当前数据库的 Schema 列表
export const listSchemas = (dsId: number): Promise<NameItem[]> =>
  client.get<NameItem[]>(`/designer/${dsId}/schemas`).then((res) => res.data);

// 列出指定 Schema 下的所有表
export const listTables = (
  dsId: number,
  schemaName?: string | null
): Promise<TableBrief[]> =>
  client
    .get<TableBrief[]>(`/designer/${dsId}/tables`, {
      params: schemaName ? { schema_name: schemaName } : undefined,
    })
    .then((res) => res.data);

// 列出指定 Schema 下的所有视图
export const listViews = (
  dsId: number,
  schemaName?: string | null
): Promise<TableBrief[]> =>
  client
    .get<TableBrief[]>(`/designer/${dsId}/views`, {
      params: schemaName ? { schema_name: schemaName } : undefined,
    })
    .then((res) => res.data);

// 读取单张表的完整元数据
export const retrieveTable = (
  dsId: number,
  tableName: string,
  schemaName?: string | null
): Promise<TableDetail> =>
  client
    .get<TableDetail>(`/designer/${dsId}/tables/${tableName}`, {
      params: schemaName ? { schema_name: schemaName } : undefined,
    })
    .then((res) => res.data);

// 反向工程：把已有表结构反射为 TableDesignSpec，供新建草稿导入
export const reverseTable = (
  dsId: number,
  tableName: string,
  schemaName?: string | null
): Promise<TableDesignSpec> =>
  client
    .get<TableDesignSpec>(`/designer/${dsId}/tables/${tableName}/reverse`, {
      params: schemaName ? { schema_name: schemaName } : undefined,
    })
    .then((res) => res.data);

// ----- 草稿 CRUD（designer+）-----

// 草稿列表（可按数据源过滤）
export const listDrafts = (datasourceId?: number): Promise<Draft[]> =>
  client
    .get<Draft[]>("/designer/drafts", {
      params: datasourceId != null ? { datasource_id: datasourceId } : undefined,
    })
    .then((res) => res.data);

// 创建草稿
export const createDraft = (data: DraftCreate): Promise<Draft> =>
  client.post<Draft>("/designer/drafts", data).then((res) => res.data);

// 草稿详情
export const retrieveDraft = (id: number): Promise<Draft> =>
  client.get<Draft>(`/designer/drafts/${id}`).then((res) => res.data);

// 更新草稿
export const updateDraft = (id: number, data: DraftUpdate): Promise<Draft> =>
  client.patch<Draft>(`/designer/drafts/${id}`, data).then((res) => res.data);

// 删除草稿
export const deleteDraft = (id: number): Promise<void> =>
  client.delete(`/designer/drafts/${id}`).then(() => undefined);

// ----- 版本管理 -----

// 版本列表（所有登录用户可读）
export const listVersions = (draftId: number): Promise<Version[]> =>
  client
    .get<Version[]>(`/designer/drafts/${draftId}/versions`)
    .then((res) => res.data);

// 回滚到指定版本（designer+）
export const rollbackToVersion = (
  draftId: number,
  versionNo: number
): Promise<Draft> =>
  client
    .post<Draft>(`/designer/drafts/${draftId}/versions/${versionNo}/rollback`)
    .then((res) => res.data);

// ----- DDL 预览与执行 -----

// DDL 预览（所有登录用户可读）
export const previewDDL = (data: DDLPreviewRequest): Promise<DDLResult> =>
  client.post<DDLResult>("/designer/ddl/preview", data).then((res) => res.data);

// 应用草稿：执行 DDL 到目标数据源（designer+）
export const applyDraft = (
  draftId: number,
  data: DDLExecuteRequest
): Promise<DDLResult> =>
  client
    .post<DDLResult>(`/designer/drafts/${draftId}/apply`, data)
    .then((res) => res.data);
