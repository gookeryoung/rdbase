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

// ----------------- 数据集（P9） -----------------

// 数据集响应
export interface Dataset {
  id: number;
  slug: string;
  name: string;
  description: string;
  datasource_id: number;
  table_name: string;
  schema_name: string;
  fields_whitelist: string[];
  filter_expression: Record<string, unknown>;
  aggregations: Record<string, unknown>;
  owner_id: number | null;
  is_active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

// 数据集创建请求
export interface DatasetCreate {
  slug: string;
  name: string;
  description?: string;
  datasource_id: number;
  table_name: string;
  schema_name?: string;
  fields_whitelist?: string[];
  filter_expression?: Record<string, unknown>;
  aggregations?: Record<string, unknown>;
  is_active?: boolean;
}

// 数据集更新请求（所有字段可选；更新时 version 自增）
export interface DatasetUpdate {
  slug?: string;
  name?: string;
  description?: string;
  datasource_id?: number;
  table_name?: string;
  schema_name?: string;
  fields_whitelist?: string[];
  filter_expression?: Record<string, unknown>;
  aggregations?: Record<string, unknown>;
  is_active?: boolean;
}

// 数据集列表响应
export interface DatasetList {
  items: Dataset[];
  total: number;
}

// 数据集行查询响应（与 RowListResponse 同构）
export interface DatasetRows {
  items: Record<string, unknown>[];
  total: number;
  page: number;
  page_size: number;
  columns: string[];
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

// 行新增请求
export interface RowCreate {
  values: Record<string, unknown>;
}

// 行更新请求
export interface RowUpdate {
  values: Record<string, unknown>;
}

// 单行响应
export interface RowOut {
  row: Record<string, unknown>;
}

// 通用消息响应
export interface MessageOut {
  detail: string;
}

// ----------------- SQL 查询控制台（P4-3） -----------------

// SQL 执行请求
export interface SqlExecRequest {
  sql: string;
}

// SQL 执行结果响应
export interface SqlResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowcount: number;
  elapsed_ms: number;
  read_only: boolean;
}

// 执行计划请求
export interface ExplainRequest {
  sql: string;
  analyze?: boolean;
}

// 执行计划响应
export interface ExplainResult {
  plan: string[];
  rows: Record<string, unknown>[];
  columns: string[];
  analyze: boolean;
  dialect: string;
}

// ----------------- 导入导出（P4-4） -----------------

// 导出格式
export type ExportFormat = "csv" | "xlsx" | "sql";

// SQL 结果集导出格式（表导出支持 sql，SQL 结果集导出支持 json，二者不重叠）
export type SqlExportFormat = "csv" | "json" | "xlsx";

// SQL 结果集导出请求
export interface SqlExportRequest {
  sql: string;
  format: SqlExportFormat;
}

// 导入结果
export interface ImportResult {
  success_count: number;
  failed_count: number;
  errors: string[];
}

// ----------------- 对象管理（P4-5） -----------------

// 对象类型：视图 / 存储过程 / 函数 / 触发器
export type ObjectType = "views" | "routines" | "triggers";

// 存储过程/函数类型
export type RoutineKind = "procedure" | "function";

// 视图详情
export interface ViewDetail {
  name: string;
  schema_name: string | null;
  definition: string;
}

// 存储过程/函数摘要
export interface RoutineBrief {
  name: string;
  schema_name: string | null;
  type: RoutineKind;
}

// 存储过程/函数详情
export interface RoutineDetail {
  name: string;
  schema_name: string | null;
  type: RoutineKind;
  definition: string;
}

// 触发器摘要
export interface TriggerBrief {
  name: string;
  schema_name: string | null;
  event: string;
  table: string;
  timing: string;
}

// 触发器详情
export interface TriggerDetail {
  name: string;
  schema_name: string | null;
  event: string;
  table: string;
  timing: string;
  definition: string;
}

// 对象编辑请求
export interface ObjectUpdate {
  definition: string;
  table?: string | null;
}

// ----------------- 审计日志（P5） -----------------

// 审计操作类型（与后端 AuditAction.TextChoices 对齐）
export type AuditAction =
  | "write"
  | "login"
  | "logout"
  | "datasource.create"
  | "datasource.update"
  | "datasource.delete"
  | "datasource.scan"
  | "dataset.create"
  | "dataset.update"
  | "dataset.delete"
  | "dataset.write"
  | "draft.create"
  | "draft.update"
  | "draft.delete"
  | "draft.rollback"
  | "ddl.apply"
  | "dml.insert"
  | "dml.update"
  | "dml.delete"
  | "dml.import"
  | "sql.execute"
  | "obj.alter"
  | "obj.drop"
  | "backup.create"
  | "backup.restore"
  | "audit.verify"
  | "token.create"
  | "token.revoke"
  | "token.rotate"
  | "sync.trigger"
  | "ingest.trigger"
  | "webhook.deliver";

// 审计记录来源
export type AuditSource = "middleware" | "business";

// 审计操作结果
export type AuditStatus = "success" | "failure";

// 审计日志条目
export interface AuditLog {
  id: number;
  user_id: number | null;
  username: string;
  action: AuditAction;
  source: AuditSource;
  status: AuditStatus;
  method: string;
  path: string;
  resource_type: string;
  resource_id: string;
  datasource_id: number | null;
  datasource_name: string;
  sql: string;
  row_count: number | null;
  elapsed_ms: number | null;
  ip: string | null;
  user_agent: string;
  error_message: string;
  extra: Record<string, unknown>;
  created_at: string;
}

// 审计日志分页响应
export interface AuditLogList {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

// 审计日志列表查询参数
export interface AuditLogQuery {
  user_id?: number;
  username?: string;
  action?: AuditAction;
  source?: AuditSource;
  status?: AuditStatus;
  resource_type?: string;
  datasource_id?: number;
  path?: string;
  start?: string;
  end?: string;
  page?: number;
  page_size?: number;
}

// 系统设置值类型
export type ValueType = "str" | "int" | "bool" | "json";

// 系统设置项
export interface SystemSetting {
  id: number;
  key: string;
  value: string;
  value_type: ValueType;
  description: string;
  updated_at: string;
}

// 系统设置列表
export interface SystemSettingList {
  items: SystemSetting[];
  total: number;
}

// 系统设置更新请求
export interface SystemSettingUpdate {
  value: string;
  description?: string;
}

// 加密密钥轮换请求
export interface RotateKeyRequest {
  confirm: boolean;
  new_key?: string;
}

// 加密密钥轮换响应
export interface RotateKeyResponse {
  success: boolean;
  message: string;
  rotated_count: number;
}

// ----------------- 数据同步（P6） -----------------

// 同步模式
export type SyncMode = "full" | "incremental";

// 同步配置状态
export type SyncConfigStatus = "active" | "paused" | "error";

// 同步日志状态
export type SyncLogStatus = "running" | "success" | "failed";

// 字段映射类型
export type FieldMappingType = "direct" | "constant";

// 字段映射
export interface SyncFieldMapping {
  id?: number;
  config_id?: number;
  source_field: string;
  target_field: string;
  mapping_type: FieldMappingType;
  fixed_value: string;
  is_pk: boolean;
}

// 同步配置
export interface SyncConfig {
  id: number;
  name: string;
  description: string;
  source_table: string;
  source_schema: string;
  source_db_alias: string;
  target_datasource_id: number;
  target_table: string;
  target_schema: string;
  sync_mode: SyncMode;
  status: SyncConfigStatus;
  timestamp_field: string;
  batch_size: number;
  scheduler_enabled: boolean;
  cron_expression: string;
  last_run_at: string | null;
  next_run_at: string | null;
  retry_count: number;
  max_retries: number;
  created_by_id: number | null;
  created_at: string;
  updated_at: string;
  last_sync_at: string | null;
  field_mappings: SyncFieldMapping[];
}

// 同步配置列表
export interface SyncConfigList {
  items: SyncConfig[];
  total: number;
}

// 创建同步配置请求
export interface SyncConfigCreate {
  name: string;
  description?: string;
  source_table: string;
  source_schema?: string;
  source_db_alias?: string;
  target_datasource_id: number;
  target_table: string;
  target_schema?: string;
  sync_mode?: SyncMode;
  status?: SyncConfigStatus;
  timestamp_field?: string;
  batch_size?: number;
  scheduler_enabled?: boolean;
  cron_expression?: string;
  max_retries?: number;
  field_mappings: SyncFieldMapping[];
}

// 更新同步配置请求
export interface SyncConfigUpdate {
  description?: string;
  target_table?: string;
  target_schema?: string;
  sync_mode?: SyncMode;
  status?: SyncConfigStatus;
  timestamp_field?: string;
  batch_size?: number;
  scheduler_enabled?: boolean;
  cron_expression?: string;
  max_retries?: number;
  field_mappings?: SyncFieldMapping[];
}

// 调度配置更新请求
export interface SyncScheduleUpdate {
  scheduler_enabled: boolean;
  cron_expression?: string;
  max_retries?: number;
}

// 同步预览结果
export interface SyncPreview {
  config_id: number;
  config_name: string;
  mode: SyncMode;
  total_rows: number;
  sample_rows: Record<string, unknown>[];
  target_fields: string[];
  pk_fields: string[];
  can_sync: boolean;
  error_message: string;
}

// 批量同步请求
export interface SyncBatchRequest {
  config_ids: number[];
  force_full?: boolean;
  stop_on_error?: boolean;
  confirm: boolean;
}

// 批量同步结果
export interface SyncBatchResult {
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  results: SyncResult[];
}

// 同步执行结果
export interface SyncResult {
  log_id: number;
  status: SyncLogStatus;
  mode: SyncMode;
  rows_read: number;
  rows_written: number;
  rows_skipped: number;
  error_message: string;
  duration_ms: number;
}

// 同步日志
export interface SyncLog {
  id: number;
  config_id: number;
  status: SyncLogStatus;
  mode: SyncMode;
  rows_read: number;
  rows_written: number;
  rows_skipped: number;
  error_message: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number;
}

// 同步日志列表
export interface SyncLogList {
  items: SyncLog[];
  total: number;
}

// 同步统计（监控面板）
export interface SyncStats {
  total: number;
  succeeded: number;
  partial: number;
  failed: number;
  success_rate: number;
  avg_duration_ms: number;
  total_rows_read: number;
  total_rows_written: number;
  total_rows_skipped: number;
}

// 告警级别
export type AlertLevel = "warning" | "error";

// 同步告警
export interface SyncAlert {
  id: number;
  config_id: number;
  config_name: string;
  level: AlertLevel;
  message: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  created_at: string;
}

// 同步告警列表
export interface SyncAlertList {
  items: SyncAlert[];
  total: number;
  unacknowledged: number;
}

// 源表列信息
export interface SourceColumnInfo {
  name: string;
  type: string;
  notnull: boolean;
  pk: boolean;
}

// 目标表列信息
export interface TargetColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  pk: boolean;
}

// ----------------- 数据爬取（P7） -----------------

// 爬取源类型
export type IngestSourceType = "api" | "html" | "file" | "rss";

// 爬取任务状态
export type IngestStatus = "active" | "paused" | "error";

// 爬取日志状态
export type IngestLogStatus = "success" | "partial" | "failed";

// 主键冲突策略
export type IngestConflictStrategy = "upsert" | "skip" | "error";

// 鉴权类型
export type IngestAuthType = "none" | "api_key" | "bearer" | "basic" | "custom";

// 爬取字段映射
export interface IngestFieldMapping {
  id?: number;
  task_id?: number;
  source_field: string;
  target_field: string;
  mapping_type: FieldMappingType;
  fixed_value: string;
  is_pk: boolean;
}

// 爬取任务
export interface IngestTask {
  id: number;
  name: string;
  description: string;
  source_type: IngestSourceType;
  source_url: string;
  parse_config: Record<string, unknown>;
  request_config: Record<string, unknown>;
  has_headers: boolean;
  auth_type: IngestAuthType;
  target_datasource_id: number;
  target_table: string;
  conflict_strategy: IngestConflictStrategy;
  batch_size: number;
  obey_robots: boolean;
  scheduler_enabled: boolean;
  cron_expression: string;
  clean_config: Record<string, unknown>;
  validation_config: Record<string, unknown>;
  next_run_at: string | null;
  last_run_at: string | null;
  last_sync_at: string | null;
  retry_count: number;
  max_retries: number;
  status: IngestStatus;
  created_by_id: number | null;
  created_at: string;
  updated_at: string;
  field_mappings: IngestFieldMapping[];
}

// 创建爬取任务请求
export interface IngestTaskCreate {
  name: string;
  description?: string;
  source_type: IngestSourceType;
  source_url: string;
  parse_config: Record<string, unknown>;
  request_config: Record<string, unknown>;
  headers?: Record<string, string>;
  auth_type?: IngestAuthType;
  target_datasource_id: number;
  target_table: string;
  conflict_strategy?: IngestConflictStrategy;
  batch_size?: number;
  obey_robots?: boolean;
  scheduler_enabled?: boolean;
  cron_expression?: string;
  clean_config?: Record<string, unknown>;
  validation_config?: Record<string, unknown>;
  field_mappings: IngestFieldMapping[];
}

// 更新爬取任务请求（全量更新，所有字段可选；headers 显式传入则覆盖）
export interface IngestTaskUpdate extends Partial<IngestTaskCreate> {
  status?: IngestStatus;
}

// 爬取日志
export interface IngestLog {
  id: number;
  task_id: number;
  status: IngestLogStatus;
  rows_read: number;
  rows_written: number;
  rows_skipped: number;
  error_message: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number;
  quality_score: number;
}

// 爬取告警
export interface IngestAlert {
  id: number;
  task_id: number;
  level: AlertLevel;
  message: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  created_at: string;
}

// 爬取执行结果（含子进程 returncode 与 stderr）
export interface IngestRunResult {
  task_id: number;
  returncode: number;
  log: IngestLog | null;
  stderr: string;
}

// 爬取统计
export interface IngestStats {
  total: number;
  succeeded: number;
  partial: number;
  failed: number;
  success_rate: number;
  avg_duration_ms: number;
  total_rows_read: number;
  total_rows_written: number;
  total_rows_skipped: number;
  avg_quality_score: number;
}

// 字段健康度（P8-Q3）
export interface IngestFieldHealth {
  field: string;
  rule: string;
  avg_pass_rate: number;
  total_checks: number;
  total_failures: number;
  last_pass_rate: number;
  last_report_at: string;
  samples: number;
}

// 数据质量报告（P8-Q2）
export interface IngestQualityReport {
  id: number;
  task_id: number;
  log_id: number;
  field: string;
  rule: string;
  total_count: number;
  passed_count: number;
  failed_count: number;
  pass_rate: number;
  failure_samples: Array<{ value: unknown; reason: string }>;
  created_at: string;
}

// 数据质量汇总摘要（P8-Q2）
export interface IngestQualitySummary {
  task_id: number;
  total_rules: number;
  avg_pass_rate: number;
  worst_field: string;
  worst_rule: string;
  total_failures: number;
  last_report_at: string | null;
}

// ----------------- 系统运维（P8） -----------------

// 健康状态
export type HealthStatus = "healthy" | "degraded" | "unhealthy";

// 组件健康检查结果
export interface ComponentStatus {
  name: string;
  status: HealthStatus;
  latency_ms: number;
  detail: string;
}

// 整体健康检查响应
export interface HealthSummary {
  status: HealthStatus;
  project: string;
  components: ComponentStatus[];
}

// 连接池状态
export interface PoolStat {
  datasource_id: number;
  datasource_name: string | null;
  status_text: string;
  pool_size: number | null;
  checked_in: number | null;
  checked_out: number | null;
  overflow: number | null;
  leak_alert: boolean;
  leak_detail: string;
}

// 连接池状态聚合
export interface PoolStatsList {
  items: PoolStat[];
  total: number;
}

// ----------------- Webhook 订阅（P9 iter-44） -----------------

// Webhook 订阅创建请求
export interface WebhookSubscriptionCreate {
  name: string;
  url: string;
  secret: string;
  events: string[];
  is_active?: boolean;
}

// Webhook 订阅更新请求（所有字段可选；secret 为空表示不更新）
export interface WebhookSubscriptionUpdate {
  name?: string;
  url?: string;
  secret?: string;
  events?: string[];
  is_active?: boolean;
}

// Webhook 订阅响应（不回显 secret）
export interface WebhookSubscription {
  id: number;
  name: string;
  url: string;
  signing_algorithm: string;
  events: string[];
  is_active: boolean;
  created_by_id: number | null;
  created_at: string;
  updated_at: string;
}

// Webhook 订阅列表响应
export interface WebhookSubscriptionList {
  items: WebhookSubscription[];
  total: number;
}

// Webhook 投递日志响应
export interface WebhookDeliveryLog {
  id: number;
  subscription_id: number;
  event_type: string;
  payload: Record<string, unknown>;
  status_code: number | null;
  retry_count: number;
  next_retry_at: string | null;
  response_body: string;
  error_message: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

// Webhook 投递日志列表响应
export interface WebhookDeliveryLogList {
  items: WebhookDeliveryLog[];
  total: number;
}

// ----------------- API Token（P9 iter-45） -----------------

// API Token 可用 scope 取值
export type ApiTokenScope =
  | "datasets:read"
  | "datasets:write"
  | "sync:trigger";

// 创建 API Token 请求
export interface ApiTokenCreate {
  name: string;
  scopes: ApiTokenScope[];
  expires_at?: string | null;
}

// 创建/轮换 API Token 响应（含明文，仅此一次返回）
export interface ApiTokenCreated {
  id: number;
  name: string;
  token: string;
  prefix: string;
  scopes: ApiTokenScope[];
  expires_at: string | null;
  is_active: boolean;
  created_at: string;
}

// API Token 列表项（不含明文）
export interface ApiTokenListItem {
  id: number;
  name: string;
  prefix: string;
  scopes: ApiTokenScope[];
  expires_at: string | null;
  last_used_at: string | null;
  is_active: boolean;
  created_by_id: number | null;
  created_at: string;
}

// API Token 列表响应
export interface ApiTokenList {
  items: ApiTokenListItem[];
  total: number;
}

// 轮换 API Token 响应（含新明文，仅此一次返回）
export interface ApiTokenRotated {
  id: number;
  name: string;
  token: string;
  prefix: string;
  is_active: boolean;
}
