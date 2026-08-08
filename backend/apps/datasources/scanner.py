"""SQLite 数据库文件自动扫描服务.

扫描指定目录（默认 ``settings.DATA_DIR``）下的 SQLite 数据库文件
（``.sqlite``/``.sqlite3``/``.db``），自动注册为 :class:`DataSource` 记录。
已注册（``database`` 字段指向同一绝对路径）的文件跳过。

仅遍历目录顶层文件，不递归子目录；MySQL/PostgreSQL 为远程服务，不在扫描范围。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .models import DataSource, EngineType

logger = logging.getLogger(__name__)

# 支持的 SQLite 文件后缀
SQLITE_SUFFIXES: tuple[str, ...] = (".sqlite", ".sqlite3", ".db")

# 自动扫描数据源的分组与标签
AUTO_SCAN_GROUP = "auto-scan"
AUTO_SCAN_TAG = "auto-scanned"


@dataclass
class ScanResult:
    """扫描结果."""

    directory: Path
    scanned: int = 0  # 扫描到的 SQLite 文件总数
    created: list[DataSource] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 跳过文件的绝对路径


def scan_sqlite_files(directory: Path | None = None) -> ScanResult:
    """扫描目录下的 SQLite 数据库文件并注册为数据源.

    Args:
        directory: 待扫描目录，默认 ``settings.DATA_DIR``。目录不存在时
            返回空结果（``scanned=0``），不抛异常。

    Returns:
        扫描结果，含新建与跳过的文件列表。

    已注册（``database`` 字段指向同一绝对路径）的文件跳过；文件名 stem 作为
    数据源名称，名称冲突时追加 ``-2``/``-3`` 数字后缀直到唯一。
    """
    target_dir = directory if directory is not None else settings.DATA_DIR
    target_dir = Path(target_dir)
    result = ScanResult(directory=target_dir)

    if not target_dir.exists():
        logger.warning("扫描目录不存在: %s", target_dir)
        return result

    # 收集已注册 SQLite 数据源的 database 绝对路径做去重
    registered: set[str] = set()
    for ds in DataSource.objects.filter(engine=EngineType.SQLITE):
        if ds.database and ds.database != ":memory:":
            registered.add(Path(ds.database).resolve().as_posix())

    # 仅遍历顶层文件，不递归子目录
    for entry in sorted(target_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in SQLITE_SUFFIXES:
            continue
        result.scanned += 1
        abs_path = entry.resolve()
        key = abs_path.as_posix()
        if key in registered:
            result.skipped.append(key)
            continue
        name = _unique_name(entry.stem)
        ds = DataSource.objects.create(
            name=name,
            engine=EngineType.SQLITE,
            database=abs_path.as_posix(),
            group=AUTO_SCAN_GROUP,
            tags=[AUTO_SCAN_TAG],
        )
        result.created.append(ds)
        registered.add(key)

    return result


def _unique_name(stem: str) -> str:
    """生成不冲突的数据源名称.

    若 ``stem`` 已被占用，追加 ``-2``/``-3`` 数字后缀直到唯一。
    """
    candidate = stem
    suffix = 2
    while DataSource.objects.filter(name=candidate).exists():
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


__all__ = [
    "AUTO_SCAN_GROUP",
    "AUTO_SCAN_TAG",
    "SQLITE_SUFFIXES",
    "ScanResult",
    "scan_sqlite_files",
]
