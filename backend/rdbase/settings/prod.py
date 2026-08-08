"""生产环境配置（敏感信息从环境变量读取）."""

from __future__ import annotations

import os

from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", SECRET_KEY)  # noqa: F405

# 生产用 PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "rdbase"),
        "USER": os.environ.get("DB_USER", "rdbase"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        # 持久连接：每个线程复用连接 60 秒，减少建连开销
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

# CORS 仅允许配置的前端域名
CORS_ALLOWED_ORIGINS = [o for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o]

# CSRF 信任来源（Django 4.0+ 需显式配置，否则 POST 会被拒绝）
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# Redis（从环境变量读取，未配置时降级为无 Redis 模式）
REDIS_URL = os.environ.get("REDIS_URL", "")
