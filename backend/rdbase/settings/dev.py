"""开发环境配置."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = True
ALLOWED_HOSTS = ["*"]

# 开发用 SQLite
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db" / "db.sqlite3",
    }
}

# CORS 允许前端开发端口
CORS_ALLOW_ALL_ORIGINS = True
