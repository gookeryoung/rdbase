import client from "./client";
import type { LoginRequest, LoginResponse, User } from "@/types";

// 登录：POST /auth/login，返回 access token 与用户信息（refresh token 由后端通过 Set-Cookie 设置）
export const login = (data: LoginRequest): Promise<LoginResponse> =>
  client.post<LoginResponse>("/auth/login", data).then((res) => res.data);

// 登出：POST /auth/logout，清除 refresh cookie
export const logout = (): Promise<{ detail: string }> =>
  client.post<{ detail: string }>("/auth/logout").then((res) => res.data);

// 刷新 access token：POST /auth/refresh，依赖 HttpOnly cookie 中的 refresh token
export const refresh = (): Promise<{ access: string }> =>
  client.post<{ access: string }>("/auth/refresh").then((res) => res.data);

// 获取当前用户：GET /auth/me
export const fetchMe = (): Promise<User> =>
  client.get<User>("/auth/me").then((res) => res.data);
