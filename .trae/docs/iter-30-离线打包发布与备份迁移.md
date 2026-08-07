# iter-30 离线打包发布与备份迁移

## 需求清单

- [x] 制定离线内网打包发布策略与脚本
- [x] 编写一键备份脚本（PostgreSQL pg_dump / SQLite 文件复制 + .env + 保留策略）
- [x] 编写迁移/恢复脚本（pg_restore / SQLite 替换 + migrate 对齐）

## 迭代目标

面向无互联网的内网环境，提供从联网打包到内网部署、备份、迁移的完整闭环脚本。产物为单一 tar.gz，解压后 `deploy.py` 一键完成虚拟环境创建、离线依赖安装、数据库迁移、静态收集与配置生成；`backup.py` 一键备份平台库与配置；`restore.py` 从备份归档恢复并自动对齐 schema。

## 离线打包策略

产物布局（`dist/rdbase-offline-<version>/`）：

```
backend/          后端代码（含 migrations、manage.py）
frontend/dist/    前端构建产物（nginx 托管）
staticfiles/      collectstatic 产物
wheels/           离线 Python wheels
requirements.txt  冻结依赖清单（uv export --frozen --no-dev --no-emit-project）
config/           .env.example、nginx.conf、gunicorn.conf.py
scripts/          deploy.py、backup.py、restore.py
README.md         离线部署说明
```

流程：前端构建 → collectstatic → 组装目录 → 导出依赖清单 → pip download wheels → 打 tar.gz。

## 改动文件清单

- [scripts/offline_pack.py](file:///f:/Dev/rdbase/scripts/offline_pack.py)：离线打包脚本。读取版本号、构建前端、collectstatic、`uv export` 冻结依赖、`pip download` 拉 wheels、组装 bundle 目录、生成 gunicorn 配置与 README、打 tar.gz 归档。
- [scripts/deploy.py](file:///f:/Dev/rdbase/scripts/deploy.py)：离线部署脚本。向上探测离线包根、创建 `.venv`、`pip install --no-index --find-links wheels` 离线安装、从模板生成 `.env`、`migrate`/`collectstatic`、可选创建超级用户、输出 gunicorn 启动命令。
- [scripts/backup.py](file:///f:/Dev/rdbase/scripts/backup.py)：一键备份脚本。按 `DB_ENGINE` 自适应 PostgreSQL（`pg_dump` custom 格式）/ SQLite（文件复制），备份 `.env`，写入 manifest（engine/timestamp/version），打 tar.gz，按 mtime 保留最近 N 份。
- [scripts/restore.py](file:///f:/Dev/rdbase/scripts/restore.py)：迁移/恢复脚本。解压归档、按 manifest 选策略恢复（`pg_restore --clean --if-exists` / SQLite 文件替换），恢复前备份当前 `.env` 到 `.env.before-restore`，恢复后 `migrate` 对齐 schema。
- [tests/test_offline_pack.py](file:///f:/Dev/rdbase/tests/test_offline_pack.py)、[tests/test_deploy.py](file:///f:/Dev/rdbase/tests/test_deploy.py)、[tests/test_backup.py](file:///f:/Dev/rdbase/tests/test_backup.py)、[tests/test_restore.py](file:///f:/Dev/rdbase/tests/test_restore.py)：纯函数 + mock subprocess 编排测试，共 41 用例。
- [Makefile](file:///f:/Dev/rdbase/Makefile)：新增 `pack` 目标（`uv run python scripts/offline_pack.py`）。
- [README.md](file:///f:/Dev/rdbase/README.md)：新增「离线内网部署」章节（打包/部署/备份与迁移）。
- [.gitignore](file:///f:/Dev/rdbase/.gitignore)：新增 `backups/`（`dist/`、`wheels/` 已忽略）。

## 关键决策与依据

1. **单一 tar.gz 产物**：一站式解压即部署，包含代码、前端产物、静态文件、wheels、配置、脚本与说明，避免内网多文件搬运。
2. **依赖冻结用 `uv export --frozen --no-dev --no-emit-project`**：仅导出运行时第三方依赖（排除项目自身与 dev），配合 `pip download` 拉 wheels；uv 创建的 venv 默认无 pip，故先 `uv pip install pip` 再 `pip download`。
3. **平台一致性约束**：wheels 二进制兼容要求打包机与目标机 OS/架构/Python 主版本一致，README 明确说明；跨平台交叉打包留作后续（见遗留事项）。
4. **部署默认创建 `.venv`**：`python -m venv`（ensurepip 自带 pip）隔离依赖，离线 `--no-index --find-links wheels` 安装；`--no-venv` 可退回系统 Python。
5. **数据库方言自适应**：`DB_ENGINE` 决定 pg/sqlite，未设置时按 `DB_HOST` 是否存在推断（有视为 PostgreSQL，无视为 SQLite）；pg 用 `pg_dump`/`pg_restore`（custom 格式），sqlite 直接复制文件。
6. **备份归档含 manifest.txt**：记录 engine/timestamp/version，恢复时按 manifest 选策略，无需猜测文件扩展名；恢复前备份当前 `.env` 到 `.env.before-restore`，恢复后自动 `migrate` 对齐 schema。
7. **保留策略**：`prune_old_backups` 按 mtime 保留最近 N 份（默认 10），避免备份堆积。
8. **脚本独立自包含**：deploy/backup/restore 各自内联 `.env` 解析与 db 检测，无跨脚本共享模块，便于 bundle 内独立运行（仅 2 处复用，未达 3 处提取阈值）。
9. **跨平台**：tarfile 创建/解压（`extractall` 兼容 3.10/3.11 无 `filter` 参数，3.12+ 用 `filter="data"`）；venv python 路径分 Windows/POSIX；遵循 `dev_run.py` 的 UTF-8 输出约定。

## 代码实现情况

### 环境变量契约（脚本统一）

- `DB_ENGINE`：`postgresql` | `sqlite`（默认按 `DB_HOST` 推断）
- `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT`：PostgreSQL 连接参数
- `SQLITE_PATH`：SQLite 文件路径（默认 `backend/db/db.sqlite3`）
- `DJANGO_SETTINGS_MODULE`：默认 `rdbase.settings.prod`

### 备份归档结构

```
rdbase-backup-<时间戳>.tar.gz
├── manifest.txt      engine=postgresql|sqlite / timestamp / version
├── db.dump           PostgreSQL（custom 格式）或 db.sqlite3（SQLite）
└── .env              运行配置
```

## 整合优化情况

- 打包/部署/备份/迁移四脚本风格统一：`from __future__ import annotations`、`pathlib.Path`、`logging` 到 stdout、`argparse`、`subprocess.run(check=True)` 禁用 `shell=True`，与 `dev_run.py` 跨平台约定一致。
- gunicorn 配置由打包脚本生成模板（`config/gunicorn.conf.py`），worker 数与超时由环境变量覆盖，与 `docker-compose.yml` 的 gunicorn 启动参数对齐。
- `.gitignore` 已忽略 `dist/`、`wheels/`、`backups/`，打包与备份产物不进入版本库。

## 测试验证结果

- `make check`：ruff/format 0 errors，pyrefly 0 errors，962 passed（新增 41），覆盖率 97.83%（≥95%，与 iter-29 持平）。
- 脚本测试覆盖：版本读取、`.env` 解析、db 引擎检测、SQLite 路径解析、`pg_dump`/`pg_restore` 命令构造、manifest 读写、归档创建/解压、保留策略、`assemble_bundle` 目录组装、`deploy.main`（mock run 验证 venv/migrate/collectstatic 调用）、`backup.main`（SQLite 端到端）、`restore.main`（SQLite 端到端 + migrate 调用）。

## 遗留事项

- **跨平台 wheels 打包**：若打包机与目标机平台不一致，需用 Docker 在匹配平台构建 wheels（当前 README 说明平台一致性要求，未实现 Docker 交叉打包）。
- **服务编排未脚本化**：当前 `deploy.py` 打印 gunicorn 启动命令，未生成 systemd unit 或 nginx 自动配置文件。
- **PostgreSQL 客户端依赖**：备份/恢复需目标机预装 postgresql-client（提供 `pg_dump`/`pg_restore`），已在 README 说明。
- req-01 中 P6 的 req-31（同步监控与告警）、req-34（P6 测试与文档）仍未完成，与本轮无关。

## 下一轮计划

无明确下一轮需求。等待用户提出新需求或反馈。
