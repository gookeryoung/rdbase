// 用户信息
export interface User {
  id: number;
  username: string;
  email?: string;
  isSuperuser?: boolean;
}

// 统一 API 响应结构
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T;
}

// 分页响应结构
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

// 登录请求参数
export interface LoginRequest {
  username: string;
  password: string;
}

// 登录响应数据
export interface LoginResponse {
  token: string;
  user: User;
}
