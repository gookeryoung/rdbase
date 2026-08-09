# 需求：数据中心对外 API 与数据库健壮性增强

## 概述

将 rdbase 从「Web 数据库管理工具」升级为「数据中心」：以 API 形式向其余应用提供稳定的数据访问与调度能力，同时强化数据库层的健壮性，达到生产级可观测、可自愈、可恢复。

## 定位

- **健壮性（P8，先行）**：深化健康检查、连接池监控、熔断重试、幂等保护、分布式锁、备份恢复 API、审计防篡改，覆盖现有 P0-P7 模块的生产短板。先夯实底座，再开放对外 API，避免在脆弱基础上叠加外部流量。
- **对外 API（P9，后置）**：在现有 JWT（绑定 Web 前端会话）之外，新增 API Token 认证，让外部应用以 Token 方式接入，通过「数据集」抽象查询/写入数据，触发 sync/ingest 调度，并订阅 Webhook 事件。复用 P8 已落地的限流/锁/熔断/审计能力。
- **互补不重叠**：对外 API 复用现有 DataSource/SyncConfig/IngestTask/ConflictStrategy/审计日志等模型与服务，不重造轮子；P8 的熔断/锁/限流横向赋能 sync/ingest/manager 所有写操作，P9 直接受益。

## 需求清单

### P8 数据库健壮性增强（里程碑：生产级可观测、可自愈、可恢复）

- [x] 36 深度健康检查 + 连接池监控：/health/live（轻量存活）与 /health/ready（DB 连通性/连接池状态/磁盘空间/Redis 连通性）分离 + /api/v1/system/pool-stats（暴露所有 SQLAlchemy 引擎池状态：size/checkedin/checkedout/overflow）+ 连接泄露检测（长事务告警）+ 前端系统状态面板
- [x] 37 熔断与重试：外部数据源连接失败的指数退避重试（max_retries 可配，复用 SyncConfig/IngestTask 现有字段）+ 熔断器（连续失败 N 次短路 M 秒，半开探测）+ 熔断状态暴露 API + sync/ingest 服务接入熔断
- [x] 38 幂等保护 + 分布式锁：sync/ingest 触发接口支持 Idempotency-Key 请求头（Redis 缓存结果 24h，重复请求返回缓存结果；幂等 key 以「认证主体维度」抽象设计，user_id 与后续 API Token 均可作 key 主体，为 P9 铺路）+ Redis 分布式锁防同一任务并发执行（锁超时 30s 自动释放，获取失败返回 409）+ 锁状态暴露 API
- [x] 39 备份恢复 API + 审计防篡改：POST /api/v1/system/backup（admin 触发，复用 scripts/backup.py 逻辑，异步执行+任务状态查询）+ GET /api/v1/system/backups（列表+下载）+ POST /api/v1/system/restore（admin 触发，需二次确认）+ 审计日志哈希链（每条 AuditLog 记录含 prev_hash 与自身 hash，篡改可检测）+ 哈希校验 API
- [x] 40 P8 测试与文档：健壮性模块端到端测试（健康检查/熔断/锁/幂等/备份恢复/哈希链）+ 压力测试（并发触发/限流边界/熔断短路）+ README 更新运维监控章节 + 部署文档补充 Redis 与健康检查配置

### P9 数据中心对外 API（里程碑：外部应用可通过 API Token 全功能访问平台数据与调度）

- [x] 41 API Token 认证机制：ApiToken 模型（name/token_hash/scopes/expires_at/last_used_at/created_by）+ 生成（仅创建时返回明文，存储 SHA-256 哈希）+ 校验中间件 ApiTokenAuth（与 JWTAuth 并存，按请求头优先级解析）+ /api/v1/tokens CRUD（仅 admin）+ 吊销与轮换。接入 P8 已实现的幂等 key 抽象（Token 自动作为幂等主体）。
- [x] 42 数据集（Dataset）抽象与查询 API：Dataset 模型（slug 唯一/绑定数据源/表名/字段白名单/过滤条件/聚合规则/owner）+ 对外只读查询端点 GET /api/v1/datasets/{slug}/rows（分页/排序/筛选/字段裁剪，复用 manager 查询能力，走 API Token 鉴权与 scope 校验）+ 前端数据集管理页（admin 可创建/编辑/删除/预览）
- [x] 43 数据集写入 API：POST /api/v1/datasets/{slug}/rows（单行/批量 UPSERT）+ 冲突策略复用（UPSERT/SKIP/ERROR）+ 写入审计（AuditAction 新增 DATASET_WRITE）+ 配额控制（每 Token 每日写入上限，超额 429）+ 速率限制（Token 维度令牌桶，Redis 实现，复用 P8 Redis 基础设施）
- [x] 44 调度触发 API + Webhook 订阅：POST /api/v1/datasets/{slug}/sync（触发绑定 sync 配置）+ POST /api/v1/ingest/tasks/{id}/trigger（外部触发爬取，复用现有 run 逻辑但走异步队列；接入 P8 分布式锁防并发、P8 幂等防重复触发）+ WebhookSubscription 模型（事件类型/目标 URL/secret/启用）+ 事件投递器（sync/ingest 完成后异步 POST，HMAC-SHA256 签名，失败指数退避重试最多 5 次）+ 前端订阅管理页
- [ ] 45 OpenAPI spec 暴露 + 速率限制完善 + P9 测试文档收尾：/api/v1/openapi.json 对外可访问（仅含数据集与触发端点，隐藏管理端点）+ 速率限制中间件（按 Token + 按端点维度，Redis 令牌桶）+ 前端 Token 管理页（列表/创建/吊销/查看 last_used_at）+ 端到端测试（API Token 全流程 + 数据集查询写入 + Webhook 投递）+ 用户手册新增「外部应用接入指南」+ API 文档汇总

## 关键架构决策

1. **健壮性先行，API 后置**：P8 先落地健康检查/熔断/锁/幂等/备份/审计哈希链，为 P9 对外 API 提供稳定的底座（限流防外部流量打垮、锁防并发、熔断防级联故障、幂等防重复、审计防篡改）。避免在脆弱基础上开放外部入口。
2. **幂等 key 抽象设计为 API Token 铺路**：P8 的 38 项幂等 key 以「认证主体维度」抽象（接口为 `get_idempotent_subject(request) -> str`），当前返回 user_id（JWT 场景），P9 的 41 项 API Token 落地后自动切为 token prefix，无需重构幂等层。
3. **API Token 与 JWT 并存不互斥**：JWTAuth 服务 Web 前端会话（Cookie 携带），ApiTokenAuth 服务外部应用（X-API-Token 请求头）。django-ninja 的 Router 级 auth 保留 JWTAuth，数据集/触发端点单独标注 ApiTokenAuth 或双认证（任一通过即可）。Token 明文仅创建时返回一次，DB 存 SHA-256 哈希，泄露后可吊销。
4. **数据集（Dataset）作为对外稳定契约**：外部应用通过 `slug`（如 `user-profiles`）访问数据，不感知底层数据源 ID/表名/字段。Dataset 配置字段白名单与过滤条件，实现「列级权限」与「行级过滤」语义。Dataset 变更走版本化，避免破坏外部调用方。
5. **Webhook 异步投递 + HMAC 签名**：sync/ingest 完成后通过后台线程（或后续 Celery）投递 Webhook，请求体含 HMAC-SHA256 签名头（用 subscription.secret），接收方校验防伪造。失败按 1s/2s/4s/8s/16s 指数退避重试 5 次，全部失败记录 WebhookDeliveryLog 供手动重投。
6. **Redis 作为基础设施（P8 引入，P9 复用）**：限流（令牌桶 lua 脚本保证原子）、分布式锁（SET NX EX + Lua 释放脚本防误释放）、幂等结果缓存（key=认证主体+idempotency-key，TTL 24h）、查询缓存（可选，Dataset 维度 TTL 60s）。dev 环境用 fakeredis 跑测试，docker-compose 新增 redis 服务。P8 完成后 P9 直接复用 Redis 客户端与工具层。
7. **熔断器本地状态 + Redis 共享**：单 worker 用本地状态（内存），多 worker 用 Redis 共享计数。熔断器三态（CLOSED/OPEN/HALF_OPEN），配置项：failure_threshold=5、open_seconds=60、half_open_max_calls=3。
8. **审计哈希链不阻断主流程**：写入 AuditLog 时计算 prev_hash（取上一条最新记录的 hash）与自身 hash（sha256(prev_hash + action + user_id + created_at + extra)）。校验 API 遍历计算并比对，发现篡改返回不一致清单。性能影响可接受（单条多一次哈希计算）。
9. **备份恢复 API 异步化**：备份可能耗时分钟级，POST 触发后立即返回 task_id，后台线程执行，GET /system/backups/{task_id} 查状态与下载 URL。恢复需 POST 两次（首次返回 confirm_token，二次携带 confirm_token 才执行），防误操作。
10. **OpenAPI spec 双视图**：/api/v1/openapi.json（管理员视图，含全部端点）与 /api/v1/datasets/openapi.json（外部视图，仅数据集+触发端点）。外部视图不暴露管理端点，避免信息泄露。

## 数据模型

### 新增模型（apps/datasources 或新 apps/datacenter）

- **ApiToken**：name（唯一）、token_hash（SHA-256）、prefix（Token 前 8 位，用于展示识别）、scopes（JSON，如 `["datasets:read","datasets:write","sync:trigger"]`）、expires_at（可空）、last_used_at、is_active、created_by（FK User）、created_at
- **Dataset**：slug（唯一，如 `user-profiles`）、name、description、datasource（FK DataSource）、table_name、fields_whitelist（JSON 字段名数组）、filter_expression（JSON，如 `{"status":"active"}`）、aggregations（JSON，预聚合规则）、owner（FK User）、is_active、version（整数，变更自增）、created_at、updated_at
- **WebhookSubscription**：name、event_types（JSON，如 `["sync.completed","ingest.completed"]`）、target_url、secret（HMAC 签名密钥，加密存储）、is_active、created_by、created_at
- **WebhookDeliveryLog**：subscription（FK）、event_type、payload（JSON）、status（pending/success/failed）、attempts（int）、last_response_code、last_error、next_retry_at、delivered_at
- **BackupTask**：name、status（pending/running/succeeded/failed）、file_path、file_size、started_at、finished_at、error_message、triggered_by（FK User）、created_at

### 修改模型

- **AuditLog**：新增 `prev_hash`（CharField 64）、`hash`（CharField 64）字段，写入时计算填充
- **SyncConfig / IngestTask**：复用现有 max_retries 字段，新增 `circuit_state`（本地缓存，不入库）与 `failure_count`（Redis 维护）

## 依赖变更（待用户授权）

pyproject.toml 新增运行时依赖：
- `redis>=5.0`（限流/锁/缓存，生产必需）
- `fakeredis>=2.20`（仅 dev/test，测试替身）

dev 依赖组新增：
- `pytest-asyncio` 已存在，无需新增

docker-compose.yml 新增 redis 服务（与现有 backend/frontend 并列）。

## 验收标准

1. `make check` 全套门禁通过（ruff + pyrefly + pytest，覆盖率 ≥ 95% 不下降）。
2. P8 健壮性：`/health/ready` 检查 DB/连接池/磁盘/Redis，任一不健康返回 503；`/health/live` 仅存活探活；熔断器连续失败触发短路返回 503；分布式锁同任务并发触发返回 409；Idempotency-Key 24h 内重复请求返回首次结果；备份可触发/下载/二次确认恢复；审计哈希链可检测篡改。
3. P9 对外 API：外部应用可通过 API Token 完成数据集查询、写入、触发 sync/ingest、接收 Webhook 全流程。
4. 速率限制：单 Token 超限返回 429 + Retry-After 头。
5. OpenAPI spec 双视图：外部视图不暴露管理端点。
6. 公共 API 有完整类型注解与中文 docstring；前端系统状态/Token/数据集/Webhook 管理页可用；README/手册同步更新。
7. 引入 Redis 后 dev 环境仍可零配置启动（fakeredis 兜底，docker-compose 可选）。

## 约束与风险

- 不得修改 `.trae/rules/` 下规则文件（除非先获用户授权）。
- 引入 Redis 属工具链变更，已在初始确认范围，按规则执行。
- API Token 与 Webhook 涉及安全边界，需充分测试 Token 吊销、scope 越权、Webhook 签名伪造等场景。
- 审计哈希链对历史数据不追溯（仅新记录有 hash），需迁移策略：历史记录 prev_hash 留空、hash 不校验。
- 备份恢复 API 涉及不可逆操作（恢复覆盖数据），必须二次确认且有审计。
- Webhook 投递不能阻塞主流程（sync/ingest 完成即返回，投递异步）。
- 分布式锁的 Redis 故障降级：Redis 不可用时锁降级为本地内存锁（单进程内互斥，记录 WARNING），不阻断业务（可配 strict 模式拒绝）。
- P8 先行可能暴露现有 sync/ingest 的隐藏缺陷（如并发竞态），需预留修复空间。

## 待用户复核项

1. API Token 的 scope 粒度：是否需要「按数据集」细粒度授权（当前设计为 `datasets:read`/`datasets:write` 全局 scope，按 Dataset.is_active 控制可见性）。
2. Webhook 投递的执行器：本期用后台线程（threading），后续是否升级为 Celery/RQ（涉及新依赖）。
3. 备份存储位置：本地文件系统（当前 scripts/backup.py 用 dbs/ 同级）vs 对象存储（S3/MinIO，需新依赖）。
4. 审计哈希链的校验时机：仅按需调用校验 API vs 定时任务自动校验告警。
