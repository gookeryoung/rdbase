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

// ----------------- 数据库设计（P3） -----------------

// 草稿状态
export type DraftStatus = "draft" | "applied" | "archived";

// 字段定义
export interface FieldSpec {
  name: string;
  type: string;
  length?: number | null;
  nullable: boolean;
  default?: string | null;
  comment?: string | null;
  primary_key: boolean;
  unique: boolean;
  autoincrement: boolean;
}

// 索引定义
export interface IndexSpec {
  name: string;
  columns: string[];
  unique: boolean;
}

// 外键定义
export interface ForeignKeySpec {
  name?: string | null;
  columns: string[];
  referred_table: string;
  referred_columns: string[];
  on_delete: string;
}

// 完整表设计规范
export interface TableDesignSpec {
  name: string;
  schema_name?: string | null;
  comment?: string | null;
  fields: FieldSpec[];
  indexes: IndexSpec[];
  foreign_keys: ForeignKeySpec[];
}

// 草稿响应
export interface Draft {
  id: number;
  name: string;
  datasource_id: number;
  table_name: string;
  schema_name: string | null;
  spec: TableDesignSpec;
  status: DraftStatus;
  created_at: string;
  updated_at: string;
}

// 草稿创建请求
export interface DraftCreate {
  name: string;
  datasource_id: number;
  table_name: string;
  schema_name?: string | null;
  spec: TableDesignSpec;
}

// 草稿更新请求（部分字段可选）
export interface DraftUpdate {
  name?: string;
  table_name?: string;
  schema_name?: string | null;
  spec?: TableDesignSpec;
}

// 版本响应
export interface Version {
  id: number;
  draft_id: number;
  version_no: number;
  spec: TableDesignSpec;
  created_at: string;
}

// DDL 预览请求
export interface DDLPreviewRequest {
  datasource_id: number;
  spec: TableDesignSpec;
  old_spec?: TableDesignSpec | null;
}

// DDL 预览/执行响应
export interface DDLResult {
  statements: string[];
  executed?: number;
}

// DDL 执行请求
export interface DDLExecuteRequest {
  old_spec?: TableDesignSpec | null;
}

// 元数据反射：库/Schema 条目
export interface NameItem {
  name: string;
}

// 表/视图摘要
export interface TableBrief {
  name: string;
  schema_name: string | null;
}

// 字段元数据
export interface ColumnMeta {
  name: string;
  type: string;
  nullable: boolean;
  default?: string | null;
  autoincrement: boolean;
  comment?: string | null;
  primary_key: boolean;
  unique: boolean;
}

// 索引元数据
export interface IndexMeta {
  name: string;
  columns: string[];
  unique: boolean;
}

// 外键元数据
export interface ForeignKeyMeta {
  name: string | null;
  columns: string[];
  referred_table: string;
  referred_schema: string | null;
  referred_columns: string[];
}

// 表完整元数据
export interface TableDetail {
  name: string;
  schema_name: string | null;
  comment: string | null;
  columns: ColumnMeta[];
  primary_key: string[];
  foreign_keys: ForeignKeyMeta[];
  indexes: IndexMeta[];
  unique_constraints: string[][];
}

// ----------------- 数据库管理（P4） -----------------

// 行筛选操作符
export type RowFilterOp =
  | "eq"
  | "ne"
  | "gt"
  | "lt"
  | "ge"
  | "le"
  | "like"
  | "in";

// 单列筛选条件
export interface RowFilter {
  op: RowFilterOp;
  val: unknown;
}

// 行查询参数
export interface RowQuery {
  schema_name?: string | null;
  page?: number;
  page_size?: number;
  order_by?: string | null;
  order_dir?: "asc" | "desc";
  columns?: string[] | null;
  filters?: Record<string, RowFilter> | null;
}

// 行列表响应
export interface RowListResponse {
  items: Record<string, unknown>[];
  total: number;
  page: number;
  page_size: number;
  columns: string[];
}
