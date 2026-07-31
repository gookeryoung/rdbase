# 需求：通用数据库管理平台

## 项目概述

开发一款基于 django-ninja + React 的通用数据库管理平台（Web 版数据库管理工具，定位类似 DBeaver/Navicat 的 Web 化形态），支持用户登录、数据库可视化设计、数据库对象管理与数据操作等全流程能力。

## 技术栈选型（待用户复核）

| 层 | 选型 | 说明 |
|----|------|------|
| 后端框架 | Django 5.x + django-ninja | Django ORM 管理平台自身数据；django-ninja 提供 类 FastAPI 的 API 路由与 Pydantic Schema |
| 认证 | django-ninja 内置 JWT + django.contrib.auth | access/refresh 双 token，HttpOnly Cookie 存放 |
| 多数据库连接 | SQLAlchemy 2.x（核心抽象层） | 统一对接 MySQL/PostgreSQL/SQLite/Oracle/SQL Server，反射 schema 与执行 SQL |
| 前端框架 | React 18 + TypeScript 5 + Vite | SPA，按需加载 |
| 前端 UI | Ant Design 5 | 管理后台生态成熟，Table/Form/Tree 组件齐全 |
| 状态管理 | Zustand | 轻量，避免 Redux 样板 |
| 可视化 ER 图 | React Flow | 节点拖拽、连线，用于表关系设计 |
| 平台数据库 | SQLite（开发）/ PostgreSQL（生产） | 存储用户、数据源连接配置、审计日志等 |
| 部署 | Docker + nginx + uvicorn/gunicorn | 前端构建产物由 nginx 托管，反代后端 API |

## 功能模块

### M1 用户与权限
- 注册/登录/登出/刷新 token
- 用户管理（管理员视角：列表/启用禁用/重置密码）
- 基于角色的权限（RBAC）：admin / designer / viewer

### M2 数据源管理
- 新增/编辑/测试/删除数据源连接（MySQL/PostgreSQL/SQLite/Oracle/SQL Server）
- 连接信息加密存储（复用平台 SECRET_KEY，Fernet 对称加密）
- 数据源分组与标签

### M3 数据库设计（可视化）
- 库/Schema/表树形浏览
- 表设计器：字段名/类型/长度/可空/默认值/注释/主键/唯一/索引
- 关系设计器：外键/多对多/一对一，React Flow ER 图拖拽连线
- DDL 预览与执行（生成 CREATE/ALTER 语句）
- 设计草稿与历史版本（平台库存储，确认后应用到目标库）

### M4 数据库管理
- 数据浏览：分页/排序/筛选/列显隐
- 数据 CRUD：新增/编辑/删除行（带事务与确认）
- SQL 查询控制台：多 Tab、语法高亮（Monaco）、执行计划、结果导出
- 导入导出：CSV / Excel / SQL 脚本
- 对象管理：视图/存储过程/函数/触发器的查看与编辑

### M5 系统管理
- 审计日志（所有 DDL/DML 操作留痕，含用户、时间、SQL、影响行数）
- 操作回放与导出
- 系统设置（会话超时、密码策略、数据源加密轮换）

## 项目结构

```
rdbase/
├── backend/                          # Django 后端
│   ├── manage.py
│   ├── pyproject.toml                # 后端独立依赖（django/django-ninja/sqlalchemy...）
│   ├── rdbase/                       # Django 项目包
│   │   ├── __init__.py
│   │   ├── settings/                 # 分环境 settings
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── dev.py
│   │   │   └── prod.py
│   │   ├── urls.py
│   │   ├── asgi.py
│   │   └── wsgi.py
│   ├── apps/
│   │   ├── accounts/                 # 用户与权限
│   │   ├── datasources/              # 数据源管理
│   │   ├── designer/                 # 数据库设计
│   │   ├── manager/                  # 数据库管理
│   │   └── audit/                    # 审计日志
│   └── api/                          # django-ninja Router 聚合
│       ├── __init__.py
│       └── v1/
│           ├── __init__.py
│           ├── accounts.py
│           ├── datasources.py
│           ├── designer.py
│           └── manager.py
├── frontend/                         # React 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── api/                      # 接口封装（axios + 类型）
│       ├── components/               # 通用组件
│       ├── layouts/                  # 布局
│       ├── pages/
│       │   ├── login/
│       │   ├── dashboard/
│       │   ├── datasources/
│       │   ├── designer/
│       │   └── manager/
│       ├── store/                    # Zustand
│       ├── routes/                   # 路由配置
│       └── types/
├── src/rdbase/                       # 原 Python 包（迁移完成后删除）
├── docker/
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   └── nginx.conf
├── docker-compose.yml
└── Makefile
```

## 开发阶段与迭代规划

初始确认为整个项目，按阶段（里程碑）推进，阶段间自动衔接。每阶段按规则 01 走「收集→计划→实现→测试→文档→验证」六步迭代循环，默认每阶段 3~5 轮迭代。

### P0 架构搭建（里程碑：可运行的空壳 + CI 通过）
- [x] 01 切换后端依赖：移除 fastapi/uvicorn，引入 django/django-ninja/sqlalchemy/uvicorn，重构 pyproject.toml 与工具链配置
- [x] 02 Django 项目初始化：项目包、分环境 settings、ASGI/WSGI、根 URL，django-ninja API 挂载
- [x] 03 React 前端初始化：Vite + React + TS + Ant Design + Zustand + axios，登录页占位、路由骨架
- [x] 04 前后端联调基础：CORS、代理（vite proxy）、健康检查接口、登录接口占位
- [x] 05 迁移 src/rdbase：删除原 FastAPI app.py，平台元信息迁入 backend，更新 Makefile/CI

### P1 用户与权限（里程碑：可登录、RBAC 生效）
- [x] 06 用户模型与认证：扩展 User 模型、注册/登录/登出/刷新 token 接口、HttpOnly Cookie
- [x] 07 RBAC 权限：角色模型、权限装饰器、前端路由守卫与按钮级权限
- [x] 08 用户管理界面：管理员列表/启用禁用/重置密码、个人中心改密
- [x] 09 P1 测试与文档：accounts 模块单元/接口测试、API 文档（django-ninja OpenAPI）、用户手册

### P2 数据源管理（里程碑：可连接外部数据库）
- [x] 10 数据源模型与加密：DataSource 模型、Fernet 加密、连接配置 Schema
- [x] 11 SQLAlchemy 连接引擎池：按数据源动态创建引擎、连接测试、健康检查
- [x] 12 数据源 CRUD 接口与界面：列表/新增/编辑/删除/测试连接、分组标签
- [x] 13 P2 测试与文档：datasources 模块测试、加密/连接池 Mock 策略、文档更新

### P3 数据库设计（里程碑：可视化建表与 ER 图）
- [x] 14 Schema 反射与元数据：库/Schema/表/字段元数据读取接口（基于 SQLAlchemy inspect）
- [x] 15 表设计器后端：字段 Schema、DDL 生成器（CREATE/ALTER）、设计草稿模型与版本
- [x] 16 表设计器前端：字段编辑表格、类型选择、索引面板、DDL 预览
- [x] 17 关系设计与 ER 图：外键/多对多配置、React Flow ER 图、连线同步 DDL
- [x] 18 P3 测试与文档：designer 模块测试、DDL 生成器多方言测试、文档更新

### P4 数据库管理（里程碑：数据 CRUD + SQL 控制台）
- [x] 19 数据浏览接口与界面：分页/排序/筛选/列显隐、行数统计
- [x] 20 数据 CRUD：新增/编辑/删除行（事务、确认、乐观锁）
- [x] 21 SQL 查询控制台：多 Tab、Monaco 编辑器、执行、结果表格、执行计划
- [x] 22 导入导出：CSV/Excel/SQL 脚本导入导出（流式处理大文件）
- [x] 23 对象管理：视图/存储过程/函数/触发器查看与编辑
- [ ] 24 P4 测试与文档：manager 模块测试、大数据量流式测试、文档更新

### P5 系统管理与部署（里程碑：可生产部署）
- [ ] 25 审计日志：操作拦截中间件、日志模型、查询界面、导出
- [ ] 26 系统设置：会话超时、密码策略、数据源加密轮换界面
- [ ] 27 Docker 化：后端/前端 Dockerfile、nginx 配置、docker-compose
- [ ] 28 生产配置与性能：gunicorn/uvicorn worker、DB 连接池、前端构建优化
- [ ] 29 P5 测试与文档：端到端测试、部署文档、README 重写、API 文档汇总

## 验收标准

1. `make check` 全套门禁通过（lint + typecheck + cov ≥ 95%）。
2. 平台支持连接 SQLite/MySQL/PostgreSQL 三种数据源（Oracle/SQL Server 留接口待后续方言接入）。
3. 用户可登录并在 RBAC 控制下完成数据库设计与管理全流程。
4. 表设计器可生成并执行正确 DDL（多方言）；ER 图可拖拽建关系。
5. SQL 控制台可执行查询并展示结果与执行计划；支持 CSV/Excel 导入导出。
6. 所有 DDL/DML 操作进入审计日志。
7. Docker 化部署可一键启动（docker-compose up）。
8. 覆盖率不低于上一轮，公共 API 有中文 docstring，文档同步更新。

## 约束与风险

- 不得修改 `.trae/rules/` 下规则文件（除非先获用户授权）。
- 引入新依赖（django/django-ninja/sqlalchemy/antd 等）属于工具链变更，按规则需在 P0 确认后执行；后续阶段的新依赖在对应迭代计划中说明。
- 多数据库方言差异是主要风险，DDL 生成与类型映射需抽象方言层并充分测试。
- 大数据量浏览/导出需流式处理，避免内存溢出。
- 凭证加密与 RBAC 需在 P1/P2 早期确立，避免后期返工。

## 待用户复核项

1. 技术栈选型（特别是前端 UI 库 Ant Design vs Material UI、状态管理 Zustand vs Redux Toolkit）。
2. 数据库支持范围（首期 SQLite/MySQL/PostgreSQL，Oracle/SQL Server 是否纳入首期）。
3. 是否需要多租户隔离（当前默认单租户，多用户共享数据源配置；若需隔离则 P1 调整）。
4. 部署形态（Docker 单机 vs K8s；当前默认 Docker 单机）。
