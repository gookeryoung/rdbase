import client from "./client";
import type {
  ApiTokenCreate,
  ApiTokenCreated,
  ApiTokenList,
  ApiTokenListItem,
  ApiTokenRotated,
  MessageOut,
} from "@/types";

// 列出全部 API Token（仅管理员，不含明文）
export const listApiTokens = (): Promise<ApiTokenList> =>
  client.get<ApiTokenList>("/tokens").then((res) => res.data);

// 创建 API Token（仅管理员，明文仅此一次返回）
export const createApiToken = (data: ApiTokenCreate): Promise<ApiTokenCreated> =>
  client.post<ApiTokenCreated>("/tokens", data).then((res) => res.data);

// 获取单个 API Token 详情（仅管理员，不含明文）
export const retrieveApiToken = (id: number): Promise<ApiTokenListItem> =>
  client.get<ApiTokenListItem>(`/tokens/${id}`).then((res) => res.data);

// 吊销 API Token（仅管理员）
export const revokeApiToken = (id: number): Promise<MessageOut> =>
  client.post<MessageOut>(`/tokens/${id}/revoke`).then((res) => res.data);

// 轮换 API Token（仅管理员，新明文仅此一次返回）
export const rotateApiToken = (id: number): Promise<ApiTokenRotated> =>
  client.post<ApiTokenRotated>(`/tokens/${id}/rotate`).then((res) => res.data);
