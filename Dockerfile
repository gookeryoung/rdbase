# CI/容器化用 Dockerfile（P0 后端基础镜像，P5 完善前后端一体化）
# 使用国内镜像源拉取基础镜像（如不需要可替换为官方镜像）
# 备选镜像源前缀：docker.1ms.run / dockerpull.com / docker.xuanyuan.me
FROM docker.m.daocloud.io/python:3.13-slim

# ---- 国内镜像源 ----
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 环境变量：非交互 + 路径配置
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/uv-cache \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# 配置 apt 国内镜像（阿里云）并安装系统依赖（含 MySQL/PostgreSQL 驱动构建依赖）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        build-essential \
        libpq-dev \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（国内镜像）
RUN pip install --no-cache-dir uv -i https://mirrors.aliyun.com/pypi/simple/

# 预装项目所需 Python 版本
RUN uv python install 3.10 3.13

# 预装项目 dev 依赖（仅复制依赖描述文件，利用 Docker 层缓存）
WORKDIR /workspace
COPY pyproject.toml uv.toml README.md ./
COPY backend/ ./backend/
COPY tests/ ./tests/

# 同步依赖到 /opt/venv（CI 时直接复用）
RUN uv sync --frozen --no-install-project 2>/dev/null || uv sync --no-install-project

# 预装 tox 环境（首尾两个版本）
RUN uvx tox run -e py310,py313 --notest 2>/dev/null || true

# 持久化 uv 缓存目录（CI 可挂载到宿主机加速）
VOLUME ["/uv-cache"]

# 默认入口
CMD ["/bin/bash"]
