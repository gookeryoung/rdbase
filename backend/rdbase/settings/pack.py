"""fspack 打包专用配置（Windows 便携模式）.

用于 fspack 打包生成 .exe + NSIS 安装包的场景：
- SQLite 便携模式：数据库文件存放在 exe 同级 data/ 目录
- 静态文件由 Django 直接服务（含前端 SPA 资源）
- 关闭 Redis（无外部依赖）
- ALLOWED_HOSTS=["*"]，CORS 全开（便携部署无固定域名）

打包后路径布局（fspack dist/）：
- rdbase.exe（C loader 启动 backend/pack_main.py）
- src/backend/rdbase/settings/pack.py（本文件）
- src/backend/staticfiles/（collectstatic 产物 + spa/ 前端构建）
- data/db.sqlite3（便携，运行时创建）
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import *  # noqa: F403
from .base import BASE_DIR, ROOT_DIR

# 打包模式：DEBUG 关闭，允许所有主机
DEBUG = False
ALLOWED_HOSTS = ["*"]

# 便携数据目录：由 pack_main.py 设置 RDBASE_DATA_DIR 环境变量
# 回退到 ROOT_DIR / "dbs" 用于开发期验证（fsp b --dry-run）
_pack_data_dir = Path(os.environ.get("RDBASE_DATA_DIR", ROOT_DIR / "dbs"))
_pack_data_dir.mkdir(parents=True, exist_ok=True)
DATA_DIR = _pack_data_dir

# SQLite 便携数据库（不依赖外部数据库服务）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

# 关闭 Redis（便携模式无外部依赖）
REDIS_URL = ""
REDIS_FAKE = False

# CORS 全开（便携部署无固定域名）
CORS_ALLOW_ALL_ORIGINS = True

# 前端构建产物目录：打包后位于 dist/src/backend/staticfiles/spa/
# Makefile fspack target 将 frontend/dist 复制到 backend/staticfiles/spa/
FRONTEND_DIST = BASE_DIR / "staticfiles" / "spa"

# 打包模式使用独立的 URL 路由（含前端 SPA 静态服务）
ROOT_URLCONF = "rdbase.pack_urls"
