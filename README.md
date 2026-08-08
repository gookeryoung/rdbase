# rdbase

> 通用数据库管理平台（django-ninja + React）。

[![CI](https://github.com/gookeryoung/rdbase/actions/workflows/ci.yml/badge.svg)](https://github.com/gookeryoung/rdbase/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Django](https://img.shields.io/badge/Django-5.2-0c4b33.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Coverage](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen.svg)

定位类似 DBeaver/Navicat 的 Web 化形态，支持用户登录、数据源管理、数据库可视化设计、数据 CRUD、SQL 控制台、审计日志等全流程能力。

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 后端框架 | Django 5.2 + django-ninja | Django ORM 管理平台自身数据；django-ninja 提供类 FastAPI 的 API 路由与 Pydantic Schema |
| 多数据库连接 | SQLAlchemy 2.x | 统一对接 MySQL/PostgreSQL/SQLite，反射 schema 与执行 SQL |
| 凭证加密 | cryptography（Fernet） | 数据源连接信息对称加密存储 |
| 前端框架 | React 18 + TypeScript 5 + Vite 5 | SPA，按需加载 |
| 前端 UI | Ant Design 5 | Table/Form/Tree 组件齐全 |
| 状态管理 | Zustand | 轻量，避免 Redux 样板 |
| 可视化 ER 图 | React Flow 11 | 节点拖拽、连线，用于表关系设计 |
| 平台数据库 | SQLite（开发）/ PostgreSQL（生产） | 存储用户、数据源配置、审计日志 |
| 部署 | Docker + nginx + uvicorn/gunicorn | 前端构建产物由 nginx 托管，反代后端 API |

## 项目结构

```
rdbase/
├── backend/                  # Django 后端
│   ├── manage.py
│   ├── rdbase/               # Django 项目包
│   │   ├── settings/         # 分环境 settings（base/dev/prod）
│   │   ├── urls.py           # /health/ + /api/v1/ + /admin/
│   │   ├── asgi.py / wsgi.py
│   │   └── __init__.py       # __version__
│   ├── apps/                 # 业务应用
│   │   ├── accounts/         # 用户与权限
│   │   ├── datasources/      # 数据源管理
│   │   ├── designer/         # 数据库设计
│   │   ├── manager/          # 数据库管理
│   │   ├── audit/            # 审计日志
│   │   ├── settings/         # 系统设置
│   │   └── sync/             # 数据同步（rdbase → 外部推送）
│   └── api/v1/               # django-ninja NinjaAPI 实例与 Router 聚合
├── frontend/                 # React 前端
│   ├── package.json
│   ├── vite.config.ts        # proxy /api、/health -> :8000
│   └── src/
│       ├── api/              # axios 封装
│       ├── components/       # 通用组件（ProtectedRoute 等）
│       ├── layouts/          # 主布局
│       ├── pages/            # 登录、Dashboard、数据源、设计器、管理器、同步等
│       ├── routes/           # 路由配置
│       ├── store/            # Zustand
│       └── types/            # TS 类型
├── docker/                   # Docker 构建文件
│   ├── Dockerfile.backend    # 后端镜像（Python 3.13 + uv + gunicorn）
│   ├── Dockerfile.frontend   # 前端镜像（Node 20 构建 + nginx 托管）
│   └── nginx.conf            # nginx 配置（静态托管 + API 反代）
├── docker-compose.yml        # 全栈一键部署编排
├── tests/                    # pytest 测试（含端到端集成测试）
├── pyproject.toml            # uv 项目依赖 + 工具链
├── ruff.toml / pyrefly.toml  # lint / 类型检查
├── pytest.ini / .coveragerc  # 测试与覆盖率
├── Makefile
└── .github/workflows/ci.yml  # GitHub Actions
```

## 开发阶段

- **P0 架构搭建**：可运行的空壳 + CI 通过（已完成）
- **P1 用户与权限**：可登录、RBAC 生效（已完成）
- **P2 数据源管理**：可连接外部数据库（已完成）
- **P3 数据库设计**：可视化建表与 ER 图（已完成）
- **P4 数据库管理**：数据 CRUD + SQL 控制台（已完成）
- **P5 系统管理与部署**：可生产部署（已完成）

完整需求见 `.trae/req/req-01-数据库管理平台.md`。

## 开发

### 环境要求

- Python ≥ 3.10
- Node.js ≥ 18
- [uv](https://docs.astral.sh/uv/) ≥ 0.5

### 后端

```bash
# 安装开发依赖
uv sync --group dev

# 应用数据库迁移
make migrate

# 启动开发服务器（0.0.0.0:8000）
make run-be
```

健康检查：`GET http://localhost:8000/health/live`（轻量存活）、`GET http://localhost:8000/health/ready`（就绪探针）
API 文档：`GET http://localhost:8000/api/v1/docs`（django-ninja Swagger UI）

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器启动在 `http://localhost:5173`，已配置 Vite proxy 将 `/api`、`/health` 请求转发到后端 `:8000`。

### Make 快捷命令

运行 `make help` 查看全部命令：

```bash
make sync        # 安装后端开发依赖
make dev         # 安装前后端全部依赖
make check       # 全套门禁 (lint + typecheck + cov ≥ 95%)
make lint        # ruff 检查 + 格式校验
make typecheck   # pyrefly 类型检查
make cov         # 测试 + 覆盖率
make migrate     # 应用数据库迁移
make run-be      # 启动后端
make run-fe      # 启动前端
make tox         # 多版本测试
make bump PART=patch  # 版本号 bump
```

## 测试

```bash
# 全套门禁（lint + typecheck + 覆盖率 ≥ 95%）
make check

# 仅运行测试
make test

# 多版本测试（tox，py310-py313）
make tox
```

## Docker 部署

一键启动全栈服务（PostgreSQL + 后端 API + 前端 nginx）：

```bash
# 1. 复制环境变量配置文件并修改
cp .env.example .env
# 编辑 .env，至少修改 DJANGO_SECRET_KEY 和 DB_PASSWORD

# 2. 构建并启动
docker-compose up -d --build

# 3. 查看服务状态
docker-compose ps

# 4. 访问
#   前端：http://localhost
#   后端 API：http://localhost:8000/api/v1/docs
#   健康检查：http://localhost:8000/health/live（存活）、/health/ready（就绪）
```

服务编排：

| 服务 | 端口 | 说明 |
|------|------|------|
| postgres | 5432 | PostgreSQL 16，平台元数据存储 |
| redis | 6379 | Redis 7，限流/分布式锁/幂等缓存/熔断共享状态 |
| backend | 8000 | Django + gunicorn（4 worker，uvicorn worker-class） |
| frontend | 80 | nginx 托管前端静态文件，反代 /api 到 backend |

可配置环境变量见 `.env.example`。

## 运维监控

P8 健壮性增强提供生产级可观测、可自愈、可恢复能力，全部运维端点需管理员权限。

### 健康检查

| 端点 | 用途 | 说明 |
|------|------|------|
| `GET /health/live` | 存活探针 | 轻量，仅返回 200（进程存活），供负载均衡探活 |
| `GET /health/ready` | 就绪探针 | 检查 DB/磁盘/Redis/连接池，任一不健康返回 503 |
| `GET /api/v1/system/health` | 详细状态 | 管理员查看各组件健康详情与延迟 |
| `GET /api/v1/system/pool-stats` | 连接池状态 | 暴露所有 SQLAlchemy 引擎池状态（size/checkedin/checkedout/overflow）+ 占用率泄露告警 |

### 熔断与重试

外部数据源调用失败时自动熔断，避免级联雪崩：

| 端点 | 用途 |
|------|------|
| `GET /api/v1/system/circuit-states` | 查看所有熔断器状态（CLOSED/OPEN/HALF_OPEN） |

熔断器配置：连续失败 5 次短路 60 秒，半开探测 3 次。sync/ingest 服务已接入熔断，OPEN 状态下请求直接拒绝不启动下游调用。

### 分布式锁与幂等

| 端点 | 用途 |
|------|------|
| `GET /api/v1/system/locks` | 查看当前持有的分布式锁 |

- **分布式锁**：sync/ingest 触发端点加锁防并发执行，锁超时 30 秒自动释放，获取失败返回 409。
- **幂等保护**：请求携带 `Idempotency-Key` 头时，24 小时内重复请求返回首次结果缓存。

### 备份恢复

| 端点 | 用途 |
|------|------|
| `POST /api/v1/system/backup` | 触发数据库备份（异步，返回 task_id） |
| `GET /api/v1/system/backups` | 列出备份归档文件 |
| `GET /api/v1/system/backups/{filename}` | 下载备份归档 |
| `GET /api/v1/system/backup-tasks/{task_id}` | 查询备份/恢复任务状态 |
| `POST /api/v1/system/restore` | 触发恢复（需 `confirm=true` 二次确认，自动创建 pre-restore 快照） |

备份复用 `scripts/backup.py` 逻辑，后台线程异步执行。恢复前自动创建当前数据库快照作为安全网。

### 审计防篡改

| 端点 | 用途 |
|------|------|
| `GET /api/v1/system/audit/verify` | 校验审计日志哈希链完整性 |

每条审计记录含 `prev_hash`（上一条记录哈希）与 `record_hash`（自身哈希），形成链式结构。任何篡改均可通过校验 API 定位断点。校验操作本身也会写入审计记录。

### Redis 配置

Redis 是健壮性模块的基础设施，用于：

- 分布式锁（跨 worker 互斥）
- 幂等结果缓存（24 小时 TTL）
- 熔断器共享状态（多 worker 一致）

| 环境变量 | 说明 |
|----------|------|
| `REDIS_URL` | Redis 连接地址，如 `redis://redis:6379/0` |
| `REDIS_FAKE` | 开发环境用 fakeredis 模拟（生产环境必须留空） |

开发环境未配置 `REDIS_URL` 时自动降级为本地内存（fakeredis 兜底），不阻断业务。生产环境建议配置 `REDIS_URL` 启用跨进程共享。

## 离线内网部署

面向无互联网的内网环境，提供一键打包、部署、备份、迁移脚本。

### 打包（联网环境）

```bash
make pack
# 或：uv run python scripts/offline_pack.py
```

产物 `dist/rdbase-offline-<version>.tar.gz`，内含后端代码、前端构建产物、collectstatic 静态文件、离线 wheels、冻结依赖清单、配置模板与部署脚本。

> 前提：打包机须与目标机 OS/架构/Python 主版本一致，以确保 wheels 二进制兼容。

### 部署（内网目标机）

```bash
tar -xzf rdbase-offline-<version>.tar.gz -C /opt/
cd /opt/rdbase-offline-<version>

# 一键部署：创建虚拟环境、离线安装依赖、迁移、收集静态、生成 .env
python scripts/deploy.py

# 编辑 .env 填入生产密钥与数据库配置后，再执行一次跳过安装的部署
python scripts/deploy.py --skip-install

# 启动后端（POSIX，先加载 .env）
. ./.env && .venv/bin/gunicorn --config config/gunicorn.conf.py rdbase.asgi:application
```

前端静态文件由 `frontend/dist` 提供，可用 nginx 托管（参考 `config/nginx.conf`）并反代 `/api` 到后端。

### 备份与迁移

```bash
# 一键备份平台库与 .env，保留最近 10 份
python scripts/backup.py --keep 10

# 从备份归档恢复（覆盖当前库与 .env，恢复后自动 migrate）
python scripts/restore.py --file backups/rdbase-backup-<时间戳>.tar.gz --yes
```

数据库类型由 `DB_ENGINE` 环境变量决定（未设置时按 `DB_HOST` 是否存在推断）：PostgreSQL 调用 `pg_dump`/`pg_restore`（需目标机安装 postgresql-client），SQLite 直接复制文件。各脚本参数详见 `--help`。

## 许可证

MIT
