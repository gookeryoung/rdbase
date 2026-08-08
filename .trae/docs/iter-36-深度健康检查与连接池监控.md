# iter-36 深度健康检查与连接池监控

## 需求清单

- [x] 新增 apps/system 应用，承载系统级运维能力
- [x] Redis 客户端单例（fakeredis 兜底，未配置降级）
- [x] 深度健康检查：DB / 磁盘 / Redis / 连接池 四类检查器
- [x] live/ready 双层探针，保留 /health/ 兼容路径
- [x] SQLAlchemy 引擎连接池状态采集 + 占用率泄露告警
- [x] 管理员 API：GET /system/health、GET /system/pool-stats
- [x] settings 注册 system 应用 + REDIS_URL/REDIS_FAKE 配置
- [x] pyproject.toml 新增 redis>=5.0 + fakeredis>=2.20
- [x] docker-compose.yml 新增 redis 服务 + backend 依赖注入 REDIS_URL
- [x] 前端 SystemStatus 页面 + api/system.ts + 类型 + 路由 + 菜单

## 迭代目标

P8 健壮性先行首项：为平台提供生产级健康检查与连接池监控能力，支撑负载均衡探活、运维诊断与泄露预警。

## 改动文件清单

### 新增

- backend/apps/system/__init__.py
- backend/apps/system/apps.py（SystemConfig）
- backend/apps/system/admin.py（空注册）
- backend/apps/system/migrations/__init__.py
- backend/apps/system/redis_client.py（单例 + fakeredis + ping + close）
- backend/apps/system/pool_monitor.py（PoolStat + collect_pool_stats + 泄露检测）
- backend/apps/system/health.py（HealthStatus + 四检查器 + build_health + live/ready view）
- backend/apps/system/schemas.py（ComponentStatusOut/HealthOut/PoolStatOut/PoolStatsOut）
- backend/apps/system/api.py（/system/health、/system/pool-stats，admin 鉴权）
- tests/test_system_redis.py（9 用例）
- tests/test_system_pool_stats.py（13 用例）
- tests/test_system_health.py（20 用例）
- frontend/src/api/system.ts
- frontend/src/pages/SystemStatus.tsx

### 修改

- backend/rdbase/urls.py：/health/ 拆为 /health/live + /health/ready，保留 /health/ 兼容
- backend/rdbase/settings/base.py：INSTALLED_APPS 加 apps.system；新增 REDIS_URL/REDIS_FAKE
- backend/rdbase/settings/prod.py：REDIS_URL 从环境变量读取
- backend/api/v1/__init__.py：挂载 system_router
- backend/apps/datasources/engine.py：未改（复用 _engine_cache/_engine_cache_lock）
- pyproject.toml：dependencies 加 redis>=5.0，dev 组加 fakeredis>=2.20
- docker-compose.yml：新增 redis 服务（redis:7-alpine + healthcheck），backend 依赖 redis 并注入 REDIS_URL
- tests/test_api_health.py：/health/ 测试改为 /health/live 轻量探针测试
- frontend/src/types/index.ts：追加 P8 类型（HealthStatus/ComponentStatus/HealthSummary/PoolStat/PoolStatsList）
- frontend/src/routes/index.tsx：加 /system-status 路由（admin）
- frontend/src/layouts/MainLayout.tsx：加"系统状态"菜单项（MonitorOutlined，admin）

## 关键决策与依据

1. **双层探针**：live 仅返回 200（进程存活，供 LB 探活不查 DB）；ready 跑全部检查器，unhealthy 返回 503（Kubernetes readiness 语义）。/health/ 保留兼容旧前端与监控。
2. **Redis 降级策略**：未配置 REDIS_URL 时 get_redis() 返回 None，check_redis 标记 degraded（非 unhealthy），系统仍可工作；REDIS_FAKE=True 时用 fakeredis（仅 dev/test）。
3. **磁盘阈值**：<100MiB 标 unhealthy，<1GiB 标 degraded，其余 healthy。DATA_DIR 路径不存在时向上回溯到已存在父目录再取 disk_usage。
4. **泄露检测近似**：SQLAlchemy QueuePool 不直接暴露单连接 checkout 时长，采用 checked_out/pool_size 占用率 >80% 标记疑似泄露（持续高占用率是泄露的强信号）。后续迭代可加 SQLAlchemy 事件监听精确跟踪。
5. **异常处理**：连接检查类用具体异常（DatabaseError/OSError/RedisError），pool_monitor 的 ORM 查询用 DatabaseError 降级；禁用裸 except Exception。redis_client 单例用 global 语句（加 noqa: PLW0603）。
6. **pool.status() 解析**：用正则提取 QueuePool 文本四字段（size/checked_in/checked_out/overflow），非 QueuePool（如 SQLite SingletonThreadPool）返回空字段不报错。
7. **管理员 API 始终 200**：/system/health 供管理员查看当前状态（不作为探针），与 /health/ready（unhealthy 返 503）职责分离。

## 代码实现情况

- redis_client.py：模块级单例 + 锁 + 初始化标记，_build_client 按 REDIS_FAKE/REDIS_URL 分支构造，ping_redis 返回 (bool, msg)，close_redis/reset_redis_client 供退出与测试。
- pool_monitor.py：PoolStat dataclass（frozen），_parse_status 正则解析，_detect_leak 占用率判定，collect_pool_stats 遍历 _engine_cache 加锁快照，_fetch_datasource_names 批量查名（DatabaseError 降级）。
- health.py：HealthStatus(str,Enum)，ComponentStatus dataclass，check_db/disk/redis/pools 四检查器，_measure 用 TypeVar 泛型测延迟，_aggregate 聚合（unhealthy>degraded>healthy），build_health 返回 dict，live_view/ready_view 视图。
- api.py：Router tags=system auth=JWTAuth()，两个视图均 require_admin，response 用 Schema。
- 前端 SystemStatus.tsx：刷新按钮 + 整体状态 Tag + 组件健康卡片（4 行）+ 连接池 Table，Promise.all 并发拉取。

## 整合优化情况

- 复用 datasources.engine 的 _engine_cache/_engine_cache_lock，未引入新缓存层。
- 前端复用 client.ts（baseURL=/api/v1）与 RoleRoute 守卫，与 Settings/Sync 等页面风格一致。
- test_api_health.py 既有 openapi/swagger 测试保留，仅替换 /health/ -> /health/live。

## 测试验证结果

- ruff check：All checks passed
- ruff format --check：177 files already formatted
- pyrefly check：0 errors（163 suppressed, 709 warnings not shown）
- pytest：1184 passed, 8 deselected，覆盖率 96.23%（>=95%）
- system 模块覆盖率：api 100%、schemas 100%、apps 100%、health 89%、pool_monitor 94%、redis_client 89%

## 遗留事项

- 前端 typecheck 因当前环境缺 node/bun 未运行验证（代码遵循现有 ts 模式，待有 node 环境时跑 `cd frontend && bun run typecheck`）。
- 泄露检测为占用率近似，后续迭代可加 SQLAlchemy checkout/checkin 事件监听精确记录单连接借出时长。
- Redis 当前仅用于健康检查，未接入 Django 缓存后端（CACHES 配置），待后续业务需要时启用。

## 下一轮计划

iter-37：进入 P8 健壮性第二项（待 req-03 需求清单确认具体方向，候选：请求限流、慢查询审计、关键操作幂等）。
