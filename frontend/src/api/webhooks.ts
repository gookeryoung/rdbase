import client from "./client";
import type {
  MessageOut,
  WebhookDeliveryLogList,
  WebhookSubscription,
  WebhookSubscriptionCreate,
  WebhookSubscriptionList,
  WebhookSubscriptionUpdate,
} from "@/types";

// 列出全部 Webhook 订阅（仅管理员）
export const listWebhookSubscriptions = (): Promise<WebhookSubscriptionList> =>
  client
    .get<WebhookSubscriptionList>("/webhooks")
    .then((res) => res.data);

// 创建 Webhook 订阅（仅管理员）
export const createWebhookSubscription = (
  data: WebhookSubscriptionCreate
): Promise<WebhookSubscription> =>
  client
    .post<WebhookSubscription>("/webhooks", data)
    .then((res) => res.data);

// 获取 Webhook 订阅详情（仅管理员）
export const retrieveWebhookSubscription = (
  id: number
): Promise<WebhookSubscription> =>
  client
    .get<WebhookSubscription>(`/webhooks/${id}`)
    .then((res) => res.data);

// 更新 Webhook 订阅（仅管理员，secret 为空表示不更新）
export const updateWebhookSubscription = (
  id: number,
  data: WebhookSubscriptionUpdate
): Promise<WebhookSubscription> =>
  client
    .patch<WebhookSubscription>(`/webhooks/${id}`, data)
    .then((res) => res.data);

// 删除 Webhook 订阅（仅管理员）
export const deleteWebhookSubscription = (id: number): Promise<MessageOut> =>
  client
    .delete<MessageOut>(`/webhooks/${id}`)
    .then((res) => res.data);

// 查询指定订阅的投递日志（仅管理员）
export const listWebhookDeliveries = (
  subscriptionId: number,
  params: {
    event_type?: string;
    limit?: number;
  } = {}
): Promise<WebhookDeliveryLogList> =>
  client
    .get<WebhookDeliveryLogList>(`/webhooks/${subscriptionId}/deliveries`, {
      params,
    })
    .then((res) => res.data);
