import client from "./client";
import type {
    RotateKeyRequest,
    RotateKeyResponse,
    SystemSetting,
    SystemSettingList,
    SystemSettingUpdate,
} from "@/types";

// 获取所有系统设置
export const listSettings = (): Promise<SystemSettingList> =>
    client.get<SystemSettingList>("/settings/settings").then((res) => res.data);

// 更新单个设置项
export const updateSetting = (
    key: string,
    payload: SystemSettingUpdate
): Promise<SystemSetting> =>
    client
        .patch<SystemSetting>(`/settings/settings/${encodeURIComponent(key)}`, payload)
        .then((res) => res.data);

// 获取预置设置项定义
export const listPresets = (): Promise<SystemSetting[]> =>
    client.get<SystemSetting[]>("/settings/settings/presets").then((res) => res.data);

// 初始化预置设置项
export const initSettings = (): Promise<{ detail: string }> =>
    client.post<{ detail: string }>("/settings/settings/init").then((res) => res.data);

// 加密密钥轮换
export const rotateKey = (payload: RotateKeyRequest): Promise<RotateKeyResponse> =>
    client
        .post<RotateKeyResponse>("/settings/rotate-key", payload)
        .then((res) => res.data);
