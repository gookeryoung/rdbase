# 需求：外部数据爬取能力（数据摄取 ingest）

## 概述

为平台新增「外部数据摄取」能力：通过 Scrapy 统一引擎，从 REST/JSON API、网页 HTML、文件下载（CSV/Excel/JSON）、RSS/Atom 四类外部源爬取数据，经字段映射后写入用户已配置的 DataSource（MySQL/PG/SQLite），支持手动触发与 cron 定时调度，全程留痕审计与告警。

## 定位

与现有 sync 模块互补：sync 为「平台库 → 外部」推送，ingest 为「外部 → 已配置数据源」拉取，方向互补、互不重叠。复用 DataSource、engine 连接池、scheduling cron、SyncLog/SyncAlert 日志告警模式与 sync_service 的方言化 UPSERT 写入逻辑，不重复造轮子。

## 需求清单

### P7 数据爬取（里程碑：4 类源可爬取并写入目标数据源）

- [x] 31 模块骨架与 Scrapy/Django 集成：ingest app 注册、4 个数据模型（IngestTask/IngestFieldMapping/IngestLog/IngestAlert）+ migration、Scrapy 子进程启动器（engine.py）、base spider、run_ingest 管理命令跑通空 Spider
- [x] 32 REST/JSON API 爬取器 + 字段映射 + 写入目标：api_spider + JSONPath 解析、pipelines 字段映射与 SQLAlchemy 批量写入（复用 ConflictStrategy 与 sync_service 方言 UPSERT）、手动执行 API、集成测试
- [x] 33 网页 HTML + 文件下载爬取器：html_spider（CSS/XPath，selectolax）、file_spider（CSV/Excel/JSON 流式下载解析）、分页/翻页规则、测试
- [x] 34 RSS/Atom 爬取器 + 鉴权 + robots 合规：rss_spider（feedparser）、请求头/API Key/Cookie 鉴权（加密存储）、robots.txt 开关与审计、增量去重、测试
- [x] 35 定时调度 + 日志告警 + 前端管理界面 + 文档：复用 cron 调度、IngestLog/IngestAlert + 监控接口、前端任务管理页、测试补全覆盖率≥95%、README/手册更新

## 关键架构决策

1. **Scrapy 在独立进程运行**：Scrapy 依赖 Twisted reactor，与 Django ASGI 共存冲突。Django web 进程通过 subprocess（禁用 shell=True，list 形参）启动管理命令 `run_ingest <task_id>`，Scrapy CrawlerProcess 在子进程内运行，reactor 隔离不污染 web 进程。定时调度由 `run_scheduled_ingest` 命令滚动触发到期任务。
2. **配置驱动 + 策略分派**：IngestTask 存储全部爬取配置；4 种源类型各实现一个 Spider + 解析器，通过 source_type 分派。
3. **Item Pipeline 落库**：Scrapy Item 经字段映射后，复用 sync_service 的方言化 UPSERT/SKIP 写入目标 DataSource 表。
4. **凭证安全**：请求头/API Key/Cookie 敏感值经 Fernet 加密存入 headers_encrypted，复用 datasources.crypto；日志脱敏。
5. **合规**：默认遵守 robots.txt（Scrapy RobotsTxtMiddleware），可按任务关闭并记录审计。

## 数据模型

- IngestTask：name、source_type（API/HTML/FILE/RSS）、source_url、解析配置（JSONField）、请求配置（headers_encrypted、method、body、auth_type）、分页配置、target_datasource（FK）、target_table、conflict_strategy、batch_size、scheduler_enabled、cron_expression、next_run_at、status、created_by
- IngestFieldMapping：config FK、source_field、target_field、mapping_type（direct/constant）、is_pk
- IngestLog：config FK、status、rows_read/written/skipped、error_message、started_at/finished_at/duration_ms
- IngestAlert：config FK、level、message、acknowledged（复用 SyncAlert 模式）

## 依赖变更（已获用户授权）

pyproject.toml 新增运行时依赖：scrapy、selectolax、feedparser、jsonpath-ng。

## 验收标准

1. `make check` 全套门禁通过（ruff + pyrefly + pytest，覆盖率 ≥ 95% 不下降）。
2. 4 类源各有一个真实可跑的端到端用例，数据正确写入目标 DataSource 表。
3. 手动触发与 cron 定时调度均可正常执行并滚动 next_run_at。
4. 字段映射、冲突策略（upsert/skip/error）行为与 sync 模块一致。
5. 凭证加密存储，日志脱敏；robots.txt 默认遵守。
6. 公共 API 有完整类型注解与中文 docstring；前端管理界面可用；README/手册同步更新。
