# Makefile - rdbase 项目快捷命令
# 运行 `make help` 查看所有可用命令

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COV_THRESHOLD := 95

.PHONY: help sync dev test cov lint typecheck check migrate makemigrations run-be run-fe doc tox bump patch minor major push pack fspack

help: ## 显示帮助信息
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z].*:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## 安装后端开发依赖
	uv sync --group dev --extra mysql

dev: ## 安装前后端全部依赖
	uv sync --group dev --extra mysql
	cd $(FRONTEND_DIR) && bun install

test: ## 运行测试（不含覆盖率）
	uv run pytest -m "not slow"

cov: ## 运行测试并检查覆盖率
	uv run pytest -m "not slow" --cov=backend --cov-fail-under=$(COV_THRESHOLD)

lint: ## 代码风格检查 (ruff)
	uv run ruff check backend tests
	uv run ruff format --check backend tests

typecheck: ## 类型检查 (pyrefly)
	uv run pyrefly check

check: lint typecheck cov ## 运行全套门禁 (lint + typecheck + cov)

makemigrations: ## 生成数据库迁移
	cd $(BACKEND_DIR) && uv run python manage.py makemigrations

migrate: ## 应用数据库迁移
	cd $(BACKEND_DIR) && uv run python manage.py migrate

run-be: ## 启动后端开发服务器 (0.0.0.0:8000)
	cd $(BACKEND_DIR) && uv run python manage.py runserver 0.0.0.0:8000

run-fe: ## 启动前端开发服务器
	cd $(FRONTEND_DIR) && bun run dev

run: ## 同时启动后端和前端开发服务器
	uv run python scripts/dev_run.py

doc: ## 构建 Sphinx 文档
	uv run sphinx-build -b html docs docs/_build/html

tox: ## 多版本测试 (tox)
	uvx tox -p auto

BUMP_PART := $(filter-out bump,$(MAKECMDGOALS))

bump: ## 版本号 bump (默认 patch，用法: make bump [minor|major])
	@uvx bump-my-version bump $(if $(BUMP_PART),$(firstword $(BUMP_PART)),patch) --tag

patch minor major:
	@:

push: ## 推送代码到所有远程仓库
	@uv run python -c "import subprocess as sp; [print(f'\u63a8\u9001 {r}...',flush=True) or (sp.run(['git','push',r],check=True) and sp.run(['git','push',r,'--tags'],check=True)) for r in sp.check_output(['git','remote'],text=True).split()]"

pack: ## 构建离线发布包到 dist/（联网环境运行）
	uv run python scripts/offline_pack.py

fspack: ## fspack 打包 Windows .exe + NSIS 安装包（需联网下载 embed python + wheels）
	cd $(FRONTEND_DIR) && bun run build
	cd $(BACKEND_DIR) && uv run python manage.py collectstatic --noinput
	rm -rf $(BACKEND_DIR)/staticfiles/spa
	cp -r $(FRONTEND_DIR)/dist $(BACKEND_DIR)/staticfiles/spa
	rm -rf dist
	PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
	PIP_TRUSTED_HOST=mirrors.aliyun.com \
	fsp b --mirror aliyun --keep-module lxml.etree
	fsp p --target windows --format nsis
