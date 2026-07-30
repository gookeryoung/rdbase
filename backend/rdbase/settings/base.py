"""Django 基础配置（开发与生产共用）.

所有环境共通的配置项放此文件，敏感信息与运行时差异由子配置覆盖。
"""

from __future__ import annotations

from pathlib import Path

# 项目根目录：backend/rdbase/settings/base.py -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

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
    # "apps.accounts",
    # "apps.datasources",
    # "apps.designer",
    # "apps.manager",
    # "apps.audit",
]

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
        "NAME": BASE_DIR / "db" / "db.sqlite3",
    }
}

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
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
