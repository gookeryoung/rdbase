# iter-17 P5-3 Docker 化

## 需求清单

- [x] 后端 Dockerfile（Python 3.13 + uv + gunicorn 多阶段构建）
- [x] 前端 Dockerfile（Node 20 构建 + nginx 托管，多阶段）
- [x] nginx.conf（静态托管 + API 反代 + gzip + SPA 回退）
- [x] docker-compose.yml（postgres + backend + frontend 三服务）
- [x] .dockerignore 排除无关文件
- [x] .env.example 部署配置示例
- [x] prod.py 补充 CSRF_TRUSTED_ORIGINS 配置

## 迭代目标

实现 Docker 化一键部署（docker-compose up），满足验收标准第 7 条。

## 改动文件清单

### 新增
- `docker/Dockerfile.backend` — 后端镜像：uv 安装依赖 + gunicorn 启动
- `docker/Dockerfile.frontend` — 前端镜像：Node 构建 + nginx 托管
- `docker/nginx.conf` — nginx 配置：SPA 路由回退、API 反代、静态缓存、gzip
- `docker-compose.yml` — 三服务编排：postgres + backend + frontend
- `.dockerignore` — 排除 .git/.venv/node_modules 等
- `.env.example` — 部署环境变量示例

### 修改
- `backend/rdbase/settings/prod.py` — 新增 CSRF_TRUSTED_ORIGINS 从环境变量读取

## 关键决策与依据

### 1. 多阶段构建
后端 Dockerfile 分 builder 和 runtime 两阶段：builder 安装编译工具链编译 mysqlclient/psycopg，runtime 仅复制 .venv 和运行时库，镜像体积更小。

### 2. docker-compose 启动时自动迁移
backend 服务的 command 使用 `sh -c` 先执行 `python manage.py migrate --noinput` 再启动 gunicorn，确保数据库 schema 同步。

### 3. nginx SPA 回退
`try_files $uri $uri/ /index.html` 确保 React Router 的前端路由在刷新时不会 404。

### 4. CSRF_TRUSTED_ORIGINS
Django 4.0+ 要求显式配置 CSRF 信任来源，否则前端 POST 请求会被拒绝。从环境变量 `CSRF_TRUSTED_ORIGINS` 读取。

## 测试验证结果

- 全部 745 个测试通过
- Ruff lint 通过
- docker-compose.yml 语法正确（Docker 未安装，无法实际构建验证）

## 遗留事项

- 未实际执行 Docker 构建验证（环境未安装 Docker）
- HTTPS/TLS 证书配置未包含（需要时添加 nginx SSL 配置）
- 生产环境的 DB 连接池调优将在 P5-4 完成

## 下一轮计划

P5-4 生产配置与性能：gunicorn worker 调优、DB 连接池配置、前端构建优化
