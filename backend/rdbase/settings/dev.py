"""开发环境配置."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import DATA_DIR

DEBUG = True
ALLOWED_HOSTS = ["*"]

# 开发用 SQLite
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        # SQLite 并发写入时设置 busy_timeout 避免立即抛 "database is locked"
        # Django 默认 busy_timeout=0，并发 INSERT 会失败（如 webhook 多订阅并发投递）
        "OPTIONS": {
            "init_command": "PRAGMA busy_timeout=5000",
        },
    }
}

# CORS 允许前端开发端口
CORS_ALLOW_ALL_ORIGINS = True
