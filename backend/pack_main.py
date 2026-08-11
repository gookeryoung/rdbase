#!/usr/bin/env python
"""fspack 打包入口脚本.

启动流程：
1. 计算 dist 目录（exe 同级），创建 data/ 子目录存放 SQLite
2. 设置 DJANGO_SETTINGS_MODULE=rdbase.settings.pack
3. django.setup() 初始化应用
4. 自动执行 migrate（首次启动建表）
5. uvicorn 启动 ASGI 服务（127.0.0.1:8000）

打包后路径布局（fspack dist/）：
- rdbase.exe（C loader 启动本脚本，__file__ 为绝对路径）
- src/backend/pack_main.py（本脚本）
- src/backend/rdbase/...
- src/backend/staticfiles/spa/（前端构建产物）
- data/db.sqlite3（便携，运行时创建）

本地验证：可直接 ``python backend/pack_main.py`` 启动，data/ 回退到
仓库根 dbs/ 目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """启动打包版 rdbase 服务."""
    # 入口脚本路径：dist/src/backend/pack_main.py
    # dist 目录 = backend_dir.parent.parent（src 的上一级）
    entry_file = Path(__file__).resolve()
    backend_dir = entry_file.parent
    dist_dir = backend_dir.parent.parent

    # 便携数据目录：dist/data/
    data_dir = dist_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 把 backend/ 加入 sys.path，使 apps.* / rdbase.* 可被导入
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)

    # 设置便携数据目录（pack.py 读取此环境变量）
    os.environ["RDBASE_DATA_DIR"] = str(data_dir)

    # Django 配置模块
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rdbase.settings.pack")

    # 初始化 Django
    import django

    django.setup()

    # 首次启动自动迁移（建表）
    from django.core.management import call_command

    call_command("migrate", interactive=False)

    # 启动 ASGI 服务（uvicorn 复用已初始化的 Django 应用）
    import uvicorn

    uvicorn.run(
        "rdbase.asgi:application",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
