更新日志
=========

v0.1.0
------

- 项目初始化
- P1 用户与权限：JWT 会话登录、RBAC 四角色（admin/designer/viewer + 自定义）、
  操作审计日志、API Token 鉴权与 scope 授权。
- P2 数据源管理：MySQL/PostgreSQL/SQLite 三引擎接入、密码加密存储、连接测试、
  表结构反射、列信息读取。
- P3 数据库设计：可视化建表（类型/约束/索引/外键）、ER 图渲染、SQL 预览与执行。
- P4 数据库管理：数据 CRUD、SQL 控制台、分页/排序/筛选、行级权限与列级白名单。
- P5 系统管理与部署：Docker 一键部署、离线内网打包/部署/备份/迁移脚本、健康检查、
  熔断器、分布式锁、幂等存储、Redis 客户端单例（fakeredis 降级）。
- P6 数据同步增强：SyncConfig/SyncFieldMapping/SyncLog/SyncAlert 模型、全量与
  增量同步、定时调度闭环（croniter）、监控告警与确认、批量触发与并发、源方言化
  读写（SQLite/MySQL/PostgreSQL UPSERT）、API Token 触发端点、Webhook 事件订阅
  外发（HMAC-SHA256 签名、指数退避重试）。
- P7 数据爬取：IngestTask/IngestFieldMapping/IngestLog/IngestAlert 模型、
  API/HTML/FILE/RSS 四类源、Scrapy 引擎集成、字段映射 Pipeline、冲突策略
  （upsert/skip/error）、熔断器与重试、定时调度、监控面板。
- P8 数据清洗与质量提升：
  - Q1 清洗 Pipeline：``CleaningPipeline`` 按配置执行缺失值处理/类型转换/格式
    标准化/去重（Redis 优先 + 内存降级）/HTML 剥离/枚举映射；空配置透传；
    Scrapy ``ITEM_PIPELINES`` 注册 ``CleaningPipeline(200) → FieldMappingPipeline(300)``；
    前端任务编辑页加清洗规则配置区。
  - Q2 质量校验：``ValidationPipeline`` + 6 类规则（必填/范围/正则/枚举/唯一/
    自定义表达式安全求值）；``IngestQualityReport`` 模型（任务/字段/规则/通过率/
    失败样本）+ API + 前端质量报告页；Pipeline 顺序固定为
    ``Cleaning(200) → Validation(250) → FieldMapping(300)``。
  - Q3 质量监控告警：``IngestLog.quality_score`` 字段（0-100）、按校验通过率
    加权计算写回、可配置阈值（warning=80/critical=60）触发 ``IngestAlert``；
    ``IngestQualityReport.field_health`` 类方法按字段聚合历史质量数据；
    ``GET /ingest/field-health`` 与 ``GET /ingest/tasks/{id}/field-health`` 端点；
    前端监控面板加「平均质量分」卡片 + 「字段健康度」表格。
  - Q4 收集增强：``DatabaseIngestSpider``（SQLAlchemy 执行 SQL 逐行 yield，
    ``datasource://{id}`` 引用源数据源）；``POST /ingest/webhook/{token}`` 公开
    端点（token 自身鉴权，无 JWT 依赖）同步驱动
    ``Cleaning → Validation → FieldMapping`` 完整 pipeline 链；三种增量策略：
    ``API_UPDATED_AT``（查询参数注入）/ ``HTML_FINGERPRINT``（SHA-256 指纹跳过）/
    ``DB_TIMESTAMP``（SQL ``:last_sync_at`` 占位符）；``IngestTask`` 新增
    ``incremental_config`` 与 ``webhook_token`` 字段；``AuditAction.WEBHOOK_RECEIVE``
    审计动作；Webhook 限流（令牌桶 ``webhook:{token}`` 维度，容量 20 / 速率 2.0/s）
    与幂等（``Idempotency-Key`` 24h 缓存）。
  - Q5 文档与测试：覆盖率 ≥ 95% 回归；``external-api-guide.rst`` 新增「Webhook
    被动接收」章节；端到端测试覆盖 Database/Webhook/增量策略三类场景（``@pytest.mark.slow``）。
