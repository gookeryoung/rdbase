"""扫描本地 SQLite 数据库文件的管理命令.

扫描指定目录（默认 ``DATA_DIR``）下的 SQLite 数据库文件并注册为数据源::

    python manage.py scan_datasources
    python manage.py scan_datasources --directory /path/to/dbs

命令内部委托 :func:`apps.datasources.scanner.scan_sqlite_files`，
已注册的文件（database 字段指向同一绝对路径）会被跳过。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from apps.datasources.scanner import scan_sqlite_files


class Command(BaseCommand):
    """扫描本地 SQLite 数据库文件并注册为数据源."""

    help = "扫描本地 SQLite 数据库文件（.sqlite/.sqlite3/.db）并注册为数据源"

    def add_arguments(self, parser: Any) -> None:  # type: ignore[missing-override-decorator]
        """添加命令行参数."""
        parser.add_argument(
            "--directory",
            type=str,
            default=None,
            help="待扫描目录（默认使用 DATA_DIR 配置）",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[missing-override-decorator]  # noqa: ARG002
        """执行扫描并输出摘要."""
        directory = options.get("directory")
        target = Path(directory) if directory else None
        result = scan_sqlite_files(target)

        self.stdout.write(f"扫描目录: {result.directory}")
        self.stdout.write(f"发现文件: {result.scanned}")

        if result.created:
            self.stdout.write(self.style.SUCCESS(f"新增数据源 {len(result.created)} 个:"))
            for ds in result.created:
                self.stdout.write(f"  - {ds.name} -> {ds.database}")
        if result.skipped:
            self.stdout.write(f"跳过已注册 {len(result.skipped)} 个:")
            for path in result.skipped:
                self.stdout.write(f"  - {path}")
        if not result.created and not result.skipped:
            self.stdout.write("无新增或跳过")
