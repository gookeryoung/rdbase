"""Django 基础配置（开发与生产共用）.

所有环境共通的配置项放此文件，敏感信息与运行时差异由子配置覆盖。
"""

from __future__ import annotations

from pathlib import Path

# 项目根目录：backend/rdbase/settings/base.py -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 仓库根目录（backend/ 的上一级），用于存放跨环境的本地数据文件
ROOT_DIR = BASE_DIR.parent

# 本地数据目录：SQLite 数据库文件等运行时产物存放于此（已加入 .gitignore）
DATA_DIR = ROOT_DIR / "dbs"

# 密钥：开发可用占位，生产必须从环境变量读取
SECRET_KEY = "django-insecure-development-key-do-not-use-in-production"

# 调试模式（子配置覆盖）
DEBUG: bool = False

# 允许的主机（子配置覆盖）
ALLOWED_HOSTS: list[str] = []

# Django 应用
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    # 本地应用（后续阶段按需启用）：
    "apps.accounts",
    "apps.datasources",
    "apps.designer",
    "apps.manager",
    "apps.audit",
    "apps.settings",
    "apps.sync",
    "apps.ingest",
    "apps.system",
    "apps.webhook",
]

# 自定义用户模型（须在首次 migrate 前设置）
AUTH_USER_MODEL = "accounts.User"

# 中间件
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # 审计日志中间件：拦截写操作并记录通用审计信息（业务上下文由 view 内 log_audit 补充）
    "apps.audit.middleware.AuditMiddleware",
]

ROOT_URLCONF = "rdbase.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],  # type: ignore[implicit-any-empty-container]
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "rdbase.wsgi.application"
ASGI_APPLICATION = "rdbase.asgi.application"

# 数据库（平台自身数据，子配置覆盖）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

# Redis（缓存与会话后端，子配置覆盖）
# - REDIS_URL 为空时系统降级为无 Redis 模式
# - REDIS_FAKE=True 时使用 fakeredis（仅开发/测试）
REDIS_URL: str = ""
REDIS_FAKE: bool = False

# 数据集写入端点限流与配额（req-03 item 43）
# - RATE_LIMIT_DATASET_WRITE: 单 Token 每分钟最大写入请求数
# - DATASET_WRITE_DAILY_QUOTA: 单 Token 每日写入总行数上限
RATE_LIMIT_DATASET_WRITE: int = 60
DATASET_WRITE_DAILY_QUOTA: int = 10000

# 触发端点令牌桶限流（iter-47，sync trigger + ingest trigger 共享一个桶）
# - RATE_LIMIT_TRIGGER_CAPACITY: 桶容量，允许的突发上限
# - RATE_LIMIT_TRIGGER_REFILL_RATE: 每秒补充的令牌数（长期平均速率上限）
RATE_LIMIT_TRIGGER_CAPACITY: int = 10
RATE_LIMIT_TRIGGER_REFILL_RATE: float = 0.5

# Webhook 接收端点令牌桶限流（iter-54，按 webhook_token 维度限流）
# - RATE_LIMIT_WEBHOOK_CAPACITY: 桶容量，允许的突发上限（数据推送场景默认高于触发端点）
# - RATE_LIMIT_WEBHOOK_REFILL_RATE: 每秒补充的令牌数（长期平均速率上限）
RATE_LIMIT_WEBHOOK_CAPACITY: int = 20
RATE_LIMIT_WEBHOOK_REFILL_RATE: float = 2.0

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # 自定义可配置验证器（从 SystemSetting 读取策略）
    {"NAME": "apps.settings.validators.ConfigurablePasswordValidator"},
    {"NAME": "apps.settings.validators.PasswordHistoryValidator"},
]

# 国际化
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# 静态文件
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# 默认主键字段
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS 默认配置（子配置覆盖）
CORS_ALLOW_CREDENTIALS = True
