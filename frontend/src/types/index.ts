// 用户角色枚举（与后端 Role.TextChoices 保持一致）
export enum Role {
  ADMIN = "admin",
  DESIGNER = "designer",
  VIEWER = "viewer",
}

// 用户信息
export interface User {
  id: number;
  username: string;
  email?: string;
  role: Role;
  is_active?: boolean;
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
  access: string;
  user: User;
}

// 数据源引擎类型
export type EngineType = "mysql" | "postgresql" | "sqlite";

// 数据源响应（不含密码）
export interface DataSource {
  id: number;
  name: string;
  engine: EngineType;
  host: string;
  port: number | null;
  database: string;
  username: string;
  group: string;
  tags: string[];
  is_active: boolean;
  created_at: string; // ISO 时间
  updated_at: string;
}

// 创建数据源请求
export interface DataSourceCreate {
  name: string;
  engine: EngineType;
  host?: string;
  port?: number | null;
  database: string;
  username?: string;
  password?: string; // 明文，服务端加密
  group?: string; // 默认 "default"
  tags?: string[];
}

// 更新数据源请求（所有字段可选）
export interface DataSourceUpdate extends Partial<DataSourceCreate> {
  is_active?: boolean;
}

// 测试连接请求（临时配置）
export interface TestConnection {
  engine: EngineType;
  host?: string;
  port?: number | null;
  database: string;
  username?: string;
  password?: string;
}

// 测试连接响应
export interface TestConnectionResult {
  ok: boolean;
  detail: string;
}
