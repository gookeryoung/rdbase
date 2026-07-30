import client from "./client";
import type { Role } from "@/types";

// 用户列表项（与后端 UserOut 对齐）
export interface UserItem {
  id: number;
  username: string;
  email: string;
  role: Role;
  is_active: boolean;
}

// 获取用户列表
export const listUsers = (): Promise<UserItem[]> =>
  client.get<UserItem[]>("/users").then((res) => res.data);

// 切换用户启用/禁用状态
export const toggleUserActive = (userId: number): Promise<UserItem> =>
  client.post<UserItem>(`/users/${userId}/toggle-active`).then((res) => res.data);

// 重置用户密码
export const resetUserPassword = (userId: number, newPassword: string): Promise<{ detail: string }> =>
  client
    .post<{ detail: string }>(`/users/${userId}/reset-password`, { new_password: newPassword })
    .then((res) => res.data);

// 修改用户角色
export const updateUserRole = (userId: number, role: Role): Promise<UserItem> =>
  client.patch<UserItem>(`/users/${userId}/role`, { role }).then((res) => res.data);
