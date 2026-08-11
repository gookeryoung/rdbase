# iter-57：fspack 打包配置

## 需求清单

- [x] 配置 fspack 打包工具，Windows 生成 .exe + NSIS 安装包
- [x] 创建打包专用 Django 配置（SQLite 便携模式）
- [x] 创建 SPA 静态服务 URL 路由
- [x] 创建 fspack 入口脚本（自动迁移 + uvicorn 启动）
- [x] pyproject.toml 添加 [tool.fspack] 配置
- [x] Makefile 添加 fspack target

## 迭代目标

为 rdbase 项目配置 fspack 打包，生成 Windows 便携 .exe + NSIS 安装包。
打包产物为单一 .exe，双击启动后自动迁移数据库并在 127.0.0.1:8000 提供
Django + React SPA 服务，无需外部 Python/Node.js/Redis/PostgreSQL 依赖。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| backend/rdbase/settings/pack.py | 新增 | 打包专用 Django 配置（SQLite 便携、DEBUG=False、FRONTEND_DIST、ROOT_URLCONF=pack_urls） |
| backend/rdbase/pack_urls.py | 新增 | 打包模式 URL 路由（/static/ + SPA catch-all 回退 index.html） |
| backend/pack_main.py | 新增 | fspack 入口脚本（sys.path 设置、migrate、uvicorn 启动） |
| backend/rdbase/settings/base.py | 修改 | 新增 FRONTEND_DIST 默认值（ROOT_DIR / "frontend" / "dist"） |
| pyproject.toml | 修改 | 新增 [tool.fspack] exclude + [tool.fspack.entries] rdbase |
| Makefile | 修改 | 新增 fspack target（构建前端 + collectstatic + 复制 SPA + fsp b + fsp p） |
| tests/test_pack.py | 新增 | pack_urls/pack_main/pack 配置测试（10 个用例） |

## 关键决策与依据

### 1. 前端构建产物放置策略

**问题**：fspack 内置 `_EXCLUDE`（shutil.ignore_patterns）排除 "dist" 和 "build"
目录名，`frontend/dist/` 会被排除，无法打包。

**方案**：Makefile fspack target 中将 `frontend/dist/` 复制到
`backend/staticfiles/spa/`。"staticfiles" 不在排除列表，会被打包。
pack_urls.py 从 `FRONTEND_DIST = BASE_DIR / "staticfiles" / "spa"` 服务前端。

**依据**：不修改 vite.config.ts，不影响现有 offline_pack.py 与开发流程。

### 2. SQLite 便携数据目录

**方案**：`pack_main.py` 计算 dist 目录（`__file__.parent.parent.parent`），
创建 `dist/data/` 存放 `db.sqlite3`。通过 `RDBASE_DATA_DIR` 环境变量传递给
`pack.py` 配置。

**依据**：用户数据与 exe 同级，便携删除/备份。开发期回退到 `ROOT_DIR / "dbs"`。

### 3. SPA 路由回退

**方案**：`pack_urls.py` 的 catch-all 正则 `^(?!api/|admin/|health/|static/).*`
匹配所有非 API/admin/health/static 路径。`_spa_fallback` 视图先尝试服务文件
（如 /assets/index-xxx.js），不存在则回退 index.html。

**依据**：vite 默认 base="/"，index.html 引用 /assets/xxx.js。catch-all
从 spa/ 目录服务这些文件，无需额外 /assets/ 路由。

### 4. fspack 安装来源

**问题**：PyPI 0.3.26 版本缺少 `fspack.packaging.wheels` 模块（打包 bug）。

**方案**：从本地源码 `/home/zhou/fspack` 安装 0.3.9 版本
（`uv tool install /home/zhou/fspack`）。

### 5. 返回类型 HttpResponseBase

**方案**：`_spa_fallback` 返回类型标注为 `HttpResponseBase` 而非 `HttpResponse`。
`FileResponse` 继承 `StreamingHttpResponse` → `HttpResponseBase`，不是
`HttpResponse` 的子类。

**依据**：与 `datasets_api.py` 导出视图的返回类型模式一致。

## 代码实现情况

- `pack.py`：继承 base.py，覆盖 DATABASES（SQLite）、REDIS_URL（空）、
  CORS_ALLOW_ALL_ORIGINS（True）、FRONTEND_DIST（staticfiles/spa）、ROOT_URLCONF
- `pack_urls.py`：3 个 URL 模式（/static/ + base_urlpatterns + SPA catch-all），
  `_spa_fallback` 视图支持文件直服务与 index.html 回退
- `pack_main.py`：`main()` 函数计算路径 → sys.path → 环境变量 → django.setup →
  migrate → uvicorn.run
- `base.py`：新增 `FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"` 默认值
- `pyproject.toml`：`[tool.fspack]` exclude（node_modules/dbs/backend/media）+
  `[tool.fspack.entries]` rdbase=backend/pack_main.py
- `Makefile` fspack target：bun build → collectstatic → cp dist→spa → fsp b → fsp p

## 测试验证结果

- `tests/test_pack.py`：10 个用例全部通过
  - SPA 路由：根路径返回 index.html、assets 文件直服务、favicon、未知路由回退、404
  - 基础路由：admin 重定向、health 200、api openapi 200
  - pack_main：环境变量设置、django.setup/migrate/uvicorn 调用验证
  - pack 配置：SQLite/DEBUG/ROOT_URLCONF/FRONTEND_DIST 断言
- `make check` 全套门禁通过：lint + typecheck + 1964 passed + coverage 95.46%
- `fsp b --dry-run` 验证：1 入口、17 声明依赖、打包计划就绪

## 遗留事项

- 实际 Windows 打包需在 Windows 环境执行 `make fspack`（Linux 无法生成 .exe）
- fspack 0.3.9 从本地源码安装，PyPI 0.3.26 有 wheels 模块缺失 bug
- 依赖含 C 扩展（mysqlclient/psycopg），Windows 需对应 wheel 或编译工具链
- gunicorn/scrapy 等纯 Python 依赖在 Windows 可装但部分功能不可用

## 下一轮计划

无（本迭代为独立功能交付，无后续阶段）。
