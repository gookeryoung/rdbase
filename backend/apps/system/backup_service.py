"""备份/恢复服务.

复用 ``scripts/backup.py`` 与 ``scripts/restore.py`` 的核心函数，在后台线程中
执行备份/恢复操作，通过 :class:`~apps.system.models.BackupTask` 跟踪任务状态。

API 层调用 :func:`trigger_backup` / :func:`trigger_restore` 创建任务并启动线程，
通过 ``GET /system/backup-tasks/{id}`` 轮询状态。

设计要点：

- ``scripts/`` 目录不在 pyrefly search-path 中，使用 ``importlib.util`` 动态加载，
  避免修改 pyrefly 配置。
- 备份目录从 ``settings.BACKUP_DIR`` 读取（默认 ``ROOT_DIR/backups``），测试可用
  ``override_settings`` 重定向到临时目录。
- 恢复前自动创建 pre-restore 快照（安全网），记录到 ``BackupTask.archive_name``。
- 路径校验：``backup_file_path`` 用 ``resolve().relative_to()`` 防路径穿越。
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import tempfile
import threading
from datetime import datetime as _datetime
from datetime import timezone as _tz
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User

from .models import BackupTask

logger = logging.getLogger(__name__)

_ROOT_DIR = Path(settings.BASE_DIR).parent
_DEFAULT_BACKUP_DIR = _ROOT_DIR / "backups"


def _load_script_module(module_name: str, filename: str) -> Any:
    """从 scripts/ 目录动态加载脚本模块.

    pyrefly 的 search-path 仅含 ``backend/``，``scripts/`` 不在其中，故用
    ``importlib.util`` 加载而非 ``import``。
    """
    path = _ROOT_DIR / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_backup: Any = _load_script_module("_rdbase_backup_script", "backup.py")
_restore: Any = _load_script_module("_rdbase_restore_script", "restore.py")


def backup_dir() -> Path:
    """备份目录（从 settings.BACKUP_DIR 读取，默认 ROOT_DIR/backups）."""
    return Path(getattr(settings, "BACKUP_DIR", _DEFAULT_BACKUP_DIR))


def backup_file_path(filename: str) -> Path | None:
    """解析备份文件名到安全路径.

    防路径穿越策略：

    1. 拒绝绝对路径（``/etc/passwd``）和含 ``..`` 的路径。
    2. 用 ``resolve().relative_to()`` 校验文件在备份目录内。

    Returns:
        文件绝对路径；文件不存在或路径越界时返回 ``None``。
    """
    # 拒绝绝对路径与目录穿越
    if filename.startswith("/") or ".." in Path(filename).parts:
        return None
    base = backup_dir().resolve()
    candidate = (base / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def list_backups() -> list[dict[str, Any]]:
    """列出备份目录中的所有归档文件，按修改时间降序."""
    base = backup_dir()
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in base.glob("rdbase-backup-*.tar.gz"):
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "modified_at": _datetime.fromtimestamp(stat.st_mtime, tz=_tz.utc),
            }
        )

    def _by_time(item: dict[str, Any]) -> Any:
        return item["modified_at"]

    items.sort(key=_by_time, reverse=True)
    return items


def trigger_backup(user: User) -> BackupTask:
    """创建备份任务并在后台线程执行.

    Args:
        user: 触发备份的用户（须管理员）。

    Returns:
        创建的 :class:`BackupTask` 实例（status=PENDING，线程已启动）。
    """
    task = BackupTask.objects.create(
        requested_by=user,
        action=BackupTask.Action.BACKUP,
        status=BackupTask.Status.PENDING,
    )
    thread = threading.Thread(target=_run_backup, args=(task.pk,), daemon=True)
    thread.start()
    return task


def trigger_restore(user: User, archive_name: str) -> BackupTask:
    """创建恢复任务并在后台线程执行.

    恢复前自动创建 pre-restore 快照。需调用方确认（API 层校验 ``confirm=True``）。

    Args:
        user: 触发恢复的用户（须管理员）。
        archive_name: 备份归档文件名（在备份目录内）。

    Returns:
        创建的 :class:`BackupTask` 实例（status=PENDING，线程已启动）。
    """
    task = BackupTask.objects.create(
        requested_by=user,
        action=BackupTask.Action.RESTORE,
        status=BackupTask.Status.PENDING,
        archive_name=archive_name,
    )
    thread = threading.Thread(target=_run_restore, args=(task.pk, archive_name), daemon=True)
    thread.start()
    return task


def _create_backup_archive(prefix: str = "") -> Path:
    """执行备份核心逻辑，返回归档路径.

    复用 ``scripts/backup.py`` 的 ``merged_env``/``detect_db_engine``/
    ``backup_sqlite``/``backup_postgresql``/``create_archive`` 等函数。

    Args:
        prefix: 文件名前缀（如 ``"pre-restore-"``），默认空。
    """
    app_dir = _ROOT_DIR
    env = _backup.merged_env(app_dir)
    engine = _backup.detect_db_engine(env)
    ts = _backup.timestamp()
    work = backup_dir() / f".tmp-{ts}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        if engine == "postgresql":
            _backup.backup_postgresql(env, work / _backup._DB_DUMP_PG)
            db_member = _backup._DB_DUMP_PG
        else:
            _backup.backup_sqlite(
                _backup.sqlite_db_path(env, app_dir),
                work / _backup._DB_DUMP_SQLITE,
            )
            db_member = _backup._DB_DUMP_SQLITE

        env_file = app_dir / ".env"
        if env_file.exists():
            shutil.copy2(env_file, work / ".env")

        _backup.write_manifest(work / _backup._MANIFEST, engine, ts, _backup.read_version(app_dir))

        members = {_backup._MANIFEST: work / _backup._MANIFEST, db_member: work / db_member}
        if (work / ".env").exists():
            members[".env"] = work / ".env"

        archive_name = f"rdbase-backup-{prefix}{ts}.tar.gz"
        archive_path = backup_dir() / archive_name
        _backup.create_archive(members, archive_path)
        return archive_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_backup(task_id: int) -> None:
    """后台执行备份任务."""
    try:
        _do_backup(task_id)
    except Exception as exc:
        logger.exception("备份任务失败 task_id=%s", task_id)
        BackupTask.objects.filter(pk=task_id).update(
            status=BackupTask.Status.FAILED,
            error_message=str(exc)[:2000],
            completed_at=timezone.now(),
        )


def _do_backup(task_id: int) -> None:
    """备份核心流程（不含异常捕获，由 _run_backup 包装）."""
    task = BackupTask.objects.get(pk=task_id)
    task.status = BackupTask.Status.RUNNING
    task.save(update_fields=["status"])

    archive_path = _create_backup_archive()

    task.archive_name = archive_path.name
    task.archive_size = archive_path.stat().st_size
    task.engine = _backup.detect_db_engine(_backup.merged_env(_ROOT_DIR))
    task.status = BackupTask.Status.SUCCESS
    task.completed_at = timezone.now()
    task.save(
        update_fields=[
            "archive_name",
            "archive_size",
            "engine",
            "status",
            "completed_at",
        ]
    )
    logger.info("备份完成 task_id=%s archive=%s", task_id, archive_path.name)


def _run_restore(task_id: int, archive_name: str) -> None:
    """后台执行恢复任务."""
    try:
        _do_restore(task_id, archive_name)
    except Exception as exc:
        logger.exception("恢复任务失败 task_id=%s", task_id)
        BackupTask.objects.filter(pk=task_id).update(
            status=BackupTask.Status.FAILED,
            error_message=str(exc)[:2000],
            completed_at=timezone.now(),
        )


def _do_restore(task_id: int, archive_name: str) -> None:
    """恢复核心流程（不含异常捕获，由 _run_restore 包装）.

    步骤：

    1. 校验归档存在；
    2. 创建 pre-restore 快照（安全网）；
    3. 解压归档、读 manifest、按引擎恢复数据库；
    4. 执行 migrate 对齐 schema。
    """
    task = BackupTask.objects.get(pk=task_id)
    task.status = BackupTask.Status.RUNNING
    task.save(update_fields=["status"])

    archive_path = backup_file_path(archive_name)
    if archive_path is None:
        raise FileNotFoundError(f"备份归档不存在或路径越界: {archive_name}")

    # 创建 pre-restore 快照
    pre_restore = _create_backup_archive(prefix="pre-restore-")
    logger.info("已创建 pre-restore 快照: %s", pre_restore.name)

    app_dir = _ROOT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        _restore.extract_archive(archive_path, Path(tmp))
        manifest = _restore.read_manifest(Path(tmp) / _restore._MANIFEST)
        env = _backup.merged_env(app_dir)
        engine = manifest.get("engine", _backup.detect_db_engine(env))

        if engine == "postgresql":
            dump_file = Path(tmp) / _restore._DB_DUMP_PG
            if not dump_file.exists():
                raise FileNotFoundError(f"归档缺少 {_restore._DB_DUMP_PG}")
            _restore.restore_postgresql(env, dump_file)
        else:
            dump_file = Path(tmp) / _restore._DB_DUMP_SQLITE
            if not dump_file.exists():
                raise FileNotFoundError(f"归档缺少 {_restore._DB_DUMP_SQLITE}")
            _restore.restore_sqlite(dump_file, _backup.sqlite_db_path(env, app_dir))

        # 恢复 .env（先备份当前）
        restored_env = Path(tmp) / ".env"
        current_env = app_dir / ".env"
        if restored_env.exists():
            if current_env.exists():
                shutil.copy2(current_env, app_dir / ".env.before-restore")
            shutil.copy2(restored_env, current_env)

    # 执行 migrate 对齐 schema
    _restore.run_migrate(app_dir, _backup.merged_env(app_dir))

    task.archive_name = pre_restore.name
    task.engine = engine
    task.status = BackupTask.Status.SUCCESS
    task.completed_at = timezone.now()
    task.save(update_fields=["archive_name", "engine", "status", "completed_at"])
    logger.info("恢复完成 task_id=%s pre_restore=%s", task_id, pre_restore.name)


__all__ = [
    "backup_dir",
    "backup_file_path",
    "list_backups",
    "trigger_backup",
    "trigger_restore",
]
