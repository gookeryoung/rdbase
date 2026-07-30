#!/usr/bin/env python
"""Django 命令行管理入口."""

from __future__ import annotations

import os
import sys


def main() -> None:
    """运行 Django 管理命令."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rdbase.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("无法导入 Django，请确认已安装 django 并激活虚拟环境。") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
