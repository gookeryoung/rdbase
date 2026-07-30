import { create } from "zustand";
import type { User } from "@/types";

// 认证状态接口
interface AuthState {
  /** 当前登录 token */
  token: string | null;
  /** 当前登录用户信息 */
  user: User | null;
  /** 登录：写入 token 与用户，并持久化到 localStorage */
  login: (token: string, user: User) => void;
  /** 登出：清除 token 与用户，并移除 localStorage 记录 */
  logout: () => void;
  /** 从 localStorage 恢复状态（页面刷新后调用） */
  hydrate: () => void;
}

const TOKEN_KEY = "rdbase_token";
const USER_KEY = "rdbase_user";

// 安全读取 localStorage 中的用户信息，解析失败返回 null
const readUser = (): User | null => {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
};

// 认证 store：管理 token 与当前用户，token 持久化到 localStorage
export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: readUser(),
  login: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    set({ token, user });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, user: null });
  },
  hydrate: () => {
    set({ token: localStorage.getItem(TOKEN_KEY), user: readUser() });
  },
}));
