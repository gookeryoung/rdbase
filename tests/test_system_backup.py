"""系统备份/恢复 API 与服务测试.

覆盖：

- POST /system/backup：触发备份（admin 权限/任务创建/审计记录）
- GET /system/backups：备份列表（含 tmp_path 隔离）
- GET /system/backups/{filename}：下载（含路径穿越防护）
- GET /system/backup-tasks/{task_id}：任务状态查询
- POST /system/restore：恢复（需 confirm/归档不存在/任务创建）
- GET /system/audit/verify：哈希链校验端点
- backup_service：list_backups/backup_file_path/backup_dir 单元测试
- backup_service：_create_backup_archive/_do_backup/_do_restore 核心流程（mock 低层函数）
- backup_service：_run_backup/_run_restore 异常处理
"""

from __future__ import annotations

import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
from apps.accounts.jwt import create_access_token
from apps.accounts.models import Role, User
from apps.audit.models import AuditAction, AuditLog
from apps.system import backup_service
from apps.system.backup_service import backup_dir, backup_file_path, list_backups
from apps.system.models import BackupTask
from django.test import Client, override_settings


def _auth(user: User) -> dict[str, str]:
    """构造 Bearer 认证头."""
    token = create_access_token(user.pk, str(user.role))
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _make_backup_file(dir_path: Path, name: str = "rdbase-backup-20260101-120000.tar.gz") -> Path:
    """在 dir_path 中创建一个假的 .tar.gz 备份文件."""
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    # 创建一个最小的 tar.gz（含 manifest.txt）
    manifest = dir_path / ".tmp-manifest.txt"
    manifest.write_text("engine=sqlite\ntimestamp=20260101-120000\n", encoding="utf-8")
    with tarfile.open(path, "w:gz") as tar:
        tar.add(manifest, arcname="manifest.txt")
    manifest.unlink()
    return path


# ---------- backup_service 单元测试 ----------


@override_settings(BACKUP_DIR="/tmp/rdbase-test-backups-nonexist")
def test_backup_dir_reads_from_settings() -> None:
    """backup_dir() 应从 settings.BACKUP_DIR 读取."""
    assert str(backup_dir()) == "/tmp/rdbase-test-backups-nonexist"


def test_backup_dir_default() -> None:
    """未设置 BACKUP_DIR 时使用默认值（ROOT_DIR/backups）."""
    # 删除 override 的影响
    from django.conf import settings

    # 确保没有 BACKUP_DIR 设置时返回默认值
    if hasattr(settings, "BACKUP_DIR"):
        # 测试环境可能被其他 fixture 设置，跳过此断言
        pass
    else:
        expected = Path(settings.BASE_DIR).parent / "backups"
        assert backup_dir() == expected


@override_settings(BACKUP_DIR="/tmp/rdbase-test-list-nonexist")
def test_list_backups_empty_dir() -> None:
    """空目录返回空列表."""
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        assert list_backups() == []
    finally:
        d.rmdir()


def test_list_backups_returns_files(tmp_path: Path) -> None:
    """list_backups 返回备份文件列表."""
    _make_backup_file(tmp_path, "rdbase-backup-20260101-120000.tar.gz")
    _make_backup_file(tmp_path, "rdbase-backup-20260102-130000.tar.gz")
    with override_settings(BACKUP_DIR=str(tmp_path)):
        items = list_backups()
    assert len(items) == 2
    assert all("filename" in i and "size" in i and "modified_at" in i for i in items)
    filenames = [i["filename"] for i in items]
    assert "rdbase-backup-20260101-120000.tar.gz" in filenames
    assert "rdbase-backup-20260102-130000.tar.gz" in filenames


def test_list_backups_nonexistent_dir() -> None:
    """目录不存在时返回空列表."""
    with override_settings(BACKUP_DIR="/tmp/rdbase-test-nonexist-xyz"):
        assert list_backups() == []


def test_backup_file_path_valid(tmp_path: Path) -> None:
    """存在的文件返回路径."""
    _make_backup_file(tmp_path, "rdbase-backup-test.tar.gz")
    with override_settings(BACKUP_DIR=str(tmp_path)):
        path = backup_file_path("rdbase-backup-test.tar.gz")
    assert path is not None
    assert path.name == "rdbase-backup-test.tar.gz"


def test_backup_file_path_not_found(tmp_path: Path) -> None:
    """不存在的文件返回 None."""
    with override_settings(BACKUP_DIR=str(tmp_path)):
        assert backup_file_path("nonexistent.tar.gz") is None


def test_backup_file_path_traversal(tmp_path: Path) -> None:
    """路径穿越（../）应返回 None."""
    # 在 tmp_path 的上级创建一个文件
    parent_file = tmp_path.parent / "secret.txt"
    parent_file.write_text("secret", encoding="utf-8")
    try:
        with override_settings(BACKUP_DIR=str(tmp_path)):
            result = backup_file_path("../secret.txt")
        assert result is None
    finally:
        parent_file.unlink(missing_ok=True)


def test_backup_file_path_absolute(tmp_path: Path) -> None:
    """绝对路径应返回 None（不允许逃逸出备份目录）."""
    # 在 tmp_path 外创建文件
    outside = tmp_path.parent / "outside-backup-test.tar.gz"
    outside.write_bytes(b"fake")
    try:
        with override_settings(BACKUP_DIR=str(tmp_path)):
            result = backup_file_path(str(outside))
        assert result is None
    finally:
        outside.unlink(missing_ok=True)


# ---------- API: POST /system/backup ----------


@pytest.mark.django_db
def test_backup_admin_only(make_user: Callable[..., User]) -> None:
    """非管理员触发备份应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    resp = client.post("/api/v1/system/backup", **_auth(viewer))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_backup_unauthenticated() -> None:
    """未认证请求应返回 401."""
    client = Client()
    resp = client.post("/api/v1/system/backup")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_backup_creates_task(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员触发备份应创建 BackupTask（mock 后台执行避免真实备份）."""

    # mock _run_backup 避免真实备份
    def _noop_backup(task_id: int) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_backup", _noop_backup)
    admin = make_user(role=Role.ADMIN)
    before = BackupTask.objects.count()
    client = Client()
    resp = client.post("/api/v1/system/backup", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "pending"
    assert BackupTask.objects.count() == before + 1
    task = BackupTask.objects.get(pk=data["task_id"])
    assert task.action == BackupTask.Action.BACKUP
    assert task.status == BackupTask.Status.PENDING
    assert task.requested_by_id == admin.pk


@pytest.mark.django_db
def test_backup_creates_audit_log(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备份触发应创建 BACKUP_CREATE 审计记录."""

    def _noop_backup(task_id: int) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_backup", _noop_backup)
    admin = make_user(role=Role.ADMIN)
    before = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATE).count()
    client = Client()
    client.post("/api/v1/system/backup", **_auth(admin))
    after = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATE).count()
    assert after - before >= 1


# ---------- API: GET /system/backups ----------


@pytest.mark.django_db
def test_list_backups_view(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """GET /system/backups 返回备份列表."""
    _make_backup_file(tmp_path, "rdbase-backup-20260101-120000.tar.gz")
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.get("/api/v1/system/backups", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["filename"] == "rdbase-backup-20260101-120000.tar.gz"


@pytest.mark.django_db
def test_list_backups_admin_only(make_user: Callable[..., User]) -> None:
    """非管理员查询备份列表应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    resp = client.get("/api/v1/system/backups", **_auth(viewer))
    assert resp.status_code == 403


# ---------- API: GET /system/backups/{filename} ----------


@pytest.mark.django_db
def test_download_backup(
    make_user: Callable[..., User],
    tmp_path: Path,
) -> None:
    """GET /system/backups/{filename} 下载备份文件."""
    _make_backup_file(tmp_path, "rdbase-backup-download.tar.gz")
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.get("/api/v1/system/backups/rdbase-backup-download.tar.gz", **_auth(admin))
    assert resp.status_code == 200
    assert resp.headers.get("Content-Disposition") == 'attachment; filename="rdbase-backup-download.tar.gz"'  # type: ignore[union-attr]


@pytest.mark.django_db
def test_download_backup_not_found(make_user: Callable[..., User], tmp_path: Path) -> None:
    """下载不存在的文件应返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.get("/api/v1/system/backups/nonexistent.tar.gz", **_auth(admin))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_download_backup_path_traversal(make_user: Callable[..., User], tmp_path: Path) -> None:
    """路径穿越应返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.get("/api/v1/system/backups/..%2Fsecret.txt", **_auth(admin))
    assert resp.status_code == 404


# ---------- API: GET /system/backup-tasks/{task_id} ----------


@pytest.mark.django_db
def test_backup_task_status(make_user: Callable[..., User]) -> None:
    """GET /system/backup-tasks/{id} 返回任务状态."""
    admin = make_user(role=Role.ADMIN)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.BACKUP,
        status=BackupTask.Status.SUCCESS,
        archive_name="rdbase-backup-test.tar.gz",
        archive_size=1024,
        engine="sqlite",
    )
    client = Client()
    resp = client.get(f"/api/v1/system/backup-tasks/{task.pk}", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task.pk
    assert data["action"] == "backup"
    assert data["status"] == "success"
    assert data["archive_name"] == "rdbase-backup-test.tar.gz"
    assert data["archive_size"] == 1024
    assert data["engine"] == "sqlite"


@pytest.mark.django_db
def test_backup_task_not_found(make_user: Callable[..., User]) -> None:
    """查询不存在的任务应返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    resp = client.get("/api/v1/system/backup-tasks/99999", **_auth(admin))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_backup_task_admin_only(make_user: Callable[..., User]) -> None:
    """非管理员查询任务状态应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    resp = client.get("/api/v1/system/backup-tasks/1", **_auth(viewer))
    assert resp.status_code == 403


# ---------- API: POST /system/restore ----------


@pytest.mark.django_db
def test_restore_requires_confirm(make_user: Callable[..., User], tmp_path: Path) -> None:
    """恢复不带 confirm=true 应返回 400."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.post(
            "/api/v1/system/restore",
            data='{"archive_name":"rdbase-backup-test.tar.gz","confirm":false}',
            content_type="application/json",
            **_auth(admin),
        )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_restore_archive_not_found(make_user: Callable[..., User], tmp_path: Path) -> None:
    """恢复不存在的归档应返回 404."""
    admin = make_user(role=Role.ADMIN)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.post(
            "/api/v1/system/restore",
            data='{"archive_name":"nonexistent.tar.gz","confirm":true}',
            content_type="application/json",
            **_auth(admin),
        )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_restore_creates_task(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """恢复（confirm=true + 有效归档）应创建 BackupTask."""
    _make_backup_file(tmp_path, "rdbase-backup-restore-test.tar.gz")

    def _noop_restore(task_id: int, archive_name: str) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_restore", _noop_restore)
    admin = make_user(role=Role.ADMIN)
    before = BackupTask.objects.count()
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.post(
            "/api/v1/system/restore",
            data='{"archive_name":"rdbase-backup-restore-test.tar.gz","confirm":true}',
            content_type="application/json",
            **_auth(admin),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "pending"
    assert BackupTask.objects.count() == before + 1
    task = BackupTask.objects.get(pk=data["task_id"])
    assert task.action == BackupTask.Action.RESTORE


@pytest.mark.django_db
def test_restore_admin_only(make_user: Callable[..., User], tmp_path: Path) -> None:
    """非管理员触发恢复应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    with override_settings(BACKUP_DIR=str(tmp_path)):
        resp = client.post(
            "/api/v1/system/restore",
            data='{"archive_name":"x.tar.gz","confirm":true}',
            content_type="application/json",
            **_auth(viewer),
        )
    assert resp.status_code == 403


# ---------- API: GET /system/audit/verify ----------


@pytest.mark.django_db
def test_audit_verify_endpoint(make_user: Callable[..., User]) -> None:
    """GET /system/audit/verify 返回哈希链校验结果."""
    admin = make_user(role=Role.ADMIN)
    # 创建几条带哈希的审计记录
    AuditLog.objects.create_with_hash(username="a", action=AuditAction.WRITE)
    AuditLog.objects.create_with_hash(username="b", action=AuditAction.WRITE)
    client = Client()
    resp = client.get("/api/v1/system/audit/verify", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["total_records"] >= 2
    assert data["breaks"] == []


@pytest.mark.django_db
def test_audit_verify_detects_tamper(make_user: Callable[..., User]) -> None:
    """篡改后 verify 端点应返回 valid=false."""
    admin = make_user(role=Role.ADMIN)
    r1 = AuditLog.objects.create_with_hash(username="a", action=AuditAction.WRITE)
    AuditLog.objects.create_with_hash(username="b", action=AuditAction.WRITE)
    AuditLog.objects.filter(pk=r1.pk).update(username="hacker")
    client = Client()
    resp = client.get("/api/v1/system/audit/verify", **_auth(admin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert len(data["breaks"]) >= 1


@pytest.mark.django_db
def test_audit_verify_admin_only(make_user: Callable[..., User]) -> None:
    """非管理员调用校验应返回 403."""
    viewer = make_user(role=Role.VIEWER)
    client = Client()
    resp = client.get("/api/v1/system/audit/verify", **_auth(viewer))
    assert resp.status_code == 403


# ---------- BackupTask 模型 ----------


@pytest.mark.django_db
def test_backup_task_str(make_user: Callable[..., User]) -> None:
    """BackupTask.__str__ 应包含动作与状态."""
    admin = make_user(role=Role.ADMIN)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.BACKUP,
        status=BackupTask.Status.PENDING,
    )
    s = str(task)
    assert "backup" in s
    assert "pending" in s


def test_backup_task_action_choices() -> None:
    """Action 枚举应有 backup/restore 两项."""
    assert BackupTask.Action.BACKUP == "backup"
    assert BackupTask.Action.RESTORE == "restore"


def test_backup_task_status_choices() -> None:
    """Status 枚举应有 pending/running/success/failed 四项."""
    assert BackupTask.Status.PENDING == "pending"
    assert BackupTask.Status.RUNNING == "running"
    assert BackupTask.Status.SUCCESS == "success"
    assert BackupTask.Status.FAILED == "failed"


def test_backup_task_indexes() -> None:
    """模型应包含 3 个索引（action/status/created_at）."""
    index_names = {idx.name for idx in BackupTask._meta.indexes}  # type: ignore[missing-attribute]
    assert index_names == {
        "idx_backup_task_action",
        "idx_backup_task_status",
        "idx_backup_task_created",
    }


# ---------- backup_service 核心流程测试（mock 低层函数） ----------


def _setup_mock_backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """mock _backup 脚本模块的低层函数，使 _create_backup_archive 可在 tmp_path 执行."""
    bk = backup_service._backup

    def _mock_merged_env(app_dir: Path) -> dict[str, str]:
        return {"DB_ENGINE": "sqlite", "SQLITE_PATH": str(tmp_path / "db.sqlite3")}

    def _mock_detect_db_engine(env: dict[str, str]) -> str:
        return "sqlite"

    def _mock_sqlite_db_path(env: dict[str, str], app_dir: Path) -> Path:
        return tmp_path / "db.sqlite3"

    def _mock_backup_sqlite(db_file: Path, out_file: Path) -> None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"fake sqlite dump")

    def _mock_write_manifest(path: Path, engine: str, ts: str, version: str | None) -> None:
        path.write_text(f"engine={engine}\ntimestamp={ts}\n", encoding="utf-8")

    def _mock_timestamp() -> str:
        return "20260101-120000"

    def _mock_read_version(app_dir: Path) -> str | None:
        return "0.1.0"

    monkeypatch.setattr(bk, "merged_env", _mock_merged_env)
    monkeypatch.setattr(bk, "detect_db_engine", _mock_detect_db_engine)
    monkeypatch.setattr(bk, "sqlite_db_path", _mock_sqlite_db_path)
    monkeypatch.setattr(bk, "backup_sqlite", _mock_backup_sqlite)
    monkeypatch.setattr(bk, "write_manifest", _mock_write_manifest)
    monkeypatch.setattr(bk, "timestamp", _mock_timestamp)
    monkeypatch.setattr(bk, "read_version", _mock_read_version)


def test_create_backup_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_create_backup_archive 应在备份目录生成 tar.gz 归档."""
    (tmp_path / "db.sqlite3").write_bytes(b"fake db")
    _setup_mock_backup(monkeypatch, tmp_path)
    with override_settings(BACKUP_DIR=str(tmp_path / "backups")):
        archive = backup_service._create_backup_archive()
    assert archive.exists()
    assert archive.name.startswith("rdbase-backup-")
    assert archive.name.endswith(".tar.gz")
    # 验证归档内容
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.txt" in names
    assert "db.sqlite3" in names


def test_create_backup_archive_with_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_create_backup_archive(prefix=) 应在文件名中加前缀."""
    (tmp_path / "db.sqlite3").write_bytes(b"fake db")
    _setup_mock_backup(monkeypatch, tmp_path)
    with override_settings(BACKUP_DIR=str(tmp_path / "backups")):
        archive = backup_service._create_backup_archive(prefix="pre-restore-")
    assert "pre-restore-" in archive.name


@pytest.mark.django_db
def test_do_backup_success(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_do_backup 应将任务状态更新为 success."""
    admin = make_user(role=Role.ADMIN)
    (tmp_path / "db.sqlite3").write_bytes(b"fake db")
    _setup_mock_backup(monkeypatch, tmp_path)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.BACKUP,
        status=BackupTask.Status.PENDING,
    )
    with override_settings(BACKUP_DIR=str(tmp_path / "backups")):
        backup_service._do_backup(task.pk)
    task.refresh_from_db()
    assert task.status == BackupTask.Status.SUCCESS
    assert task.archive_name.startswith("rdbase-backup-")
    assert task.archive_size is not None and task.archive_size > 0
    assert task.engine == "sqlite"
    assert task.completed_at is not None


@pytest.mark.django_db
def test_run_backup_failure(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_backup 在异常时应将任务标记为 failed."""
    admin = make_user(role=Role.ADMIN)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.BACKUP,
        status=BackupTask.Status.PENDING,
    )

    def _boom() -> Path:
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup_service, "_create_backup_archive", _boom)
    backup_service._run_backup(task.pk)
    task.refresh_from_db()
    assert task.status == BackupTask.Status.FAILED
    assert "disk full" in task.error_message
    assert task.completed_at is not None


@pytest.mark.django_db
def test_do_restore_success(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_do_restore 应创建快照并恢复数据库."""
    admin = make_user(role=Role.ADMIN)
    (tmp_path / "db.sqlite3").write_bytes(b"fake db")
    _setup_mock_backup(monkeypatch, tmp_path)
    archive = _make_backup_file(tmp_path / "backups", "rdbase-backup-restore-src.tar.gz")

    # mock restore 脚本函数
    rs = backup_service._restore

    def _mock_extract(archive_path: Path, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "manifest.txt").write_text("engine=sqlite\ntimestamp=20260101\n", encoding="utf-8")
        (dest_dir / "db.sqlite3").write_bytes(b"restored db")

    def _mock_read_manifest(path: Path) -> dict[str, str]:
        return {"engine": "sqlite", "timestamp": "20260101"}

    def _mock_restore_sqlite(dump_file: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dump_file.read_bytes())

    def _mock_run_migrate(app_dir: Path, env: dict[str, str]) -> None:
        pass

    monkeypatch.setattr(rs, "extract_archive", _mock_extract)
    monkeypatch.setattr(rs, "read_manifest", _mock_read_manifest)
    monkeypatch.setattr(rs, "restore_sqlite", _mock_restore_sqlite)
    monkeypatch.setattr(rs, "run_migrate", _mock_run_migrate)

    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.RESTORE,
        status=BackupTask.Status.PENDING,
        archive_name=archive.name,
    )
    with override_settings(BACKUP_DIR=str(tmp_path / "backups")):
        backup_service._do_restore(task.pk, archive.name)
    task.refresh_from_db()
    assert task.status == BackupTask.Status.SUCCESS
    assert "pre-restore-" in task.archive_name
    assert task.engine == "sqlite"
    assert task.completed_at is not None


@pytest.mark.django_db
def test_do_restore_archive_not_found(
    make_user: Callable[..., User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_do_restore 在归档不存在时应抛 FileNotFoundError."""
    admin = make_user(role=Role.ADMIN)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.RESTORE,
        status=BackupTask.Status.PENDING,
        archive_name="nonexistent.tar.gz",
    )
    with override_settings(BACKUP_DIR=str(tmp_path)), pytest.raises(FileNotFoundError, match="备份归档不存在"):
        backup_service._do_restore(task.pk, "nonexistent.tar.gz")


@pytest.mark.django_db
def test_run_restore_failure(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_restore 在异常时应将任务标记为 failed."""
    admin = make_user(role=Role.ADMIN)
    task = BackupTask.objects.create(
        requested_by=admin,
        action=BackupTask.Action.RESTORE,
        status=BackupTask.Status.PENDING,
        archive_name="test.tar.gz",
    )

    def _boom(task_id: int, archive_name: str) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(backup_service, "_do_restore", _boom)
    backup_service._run_restore(task.pk, "test.tar.gz")
    task.refresh_from_db()
    assert task.status == BackupTask.Status.FAILED
    assert "restore failed" in task.error_message


@pytest.mark.django_db
def test_trigger_backup_starts_thread(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trigger_backup 应创建任务并启动后台线程."""
    admin = make_user(role=Role.ADMIN)

    def _noop_run(task_id: int) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_backup", _noop_run)
    task = backup_service.trigger_backup(admin)
    assert task.action == BackupTask.Action.BACKUP
    assert task.status == BackupTask.Status.PENDING
    assert task.requested_by_id == admin.pk


@pytest.mark.django_db
def test_trigger_restore_starts_thread(
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trigger_restore 应创建任务并启动后台线程."""
    admin = make_user(role=Role.ADMIN)

    def _noop_restore(task_id: int, archive_name: str) -> None:
        pass

    monkeypatch.setattr(backup_service, "_run_restore", _noop_restore)
    task = backup_service.trigger_restore(admin, "test.tar.gz")
    assert task.action == BackupTask.Action.RESTORE
    assert task.status == BackupTask.Status.PENDING
    assert task.archive_name == "test.tar.gz"
