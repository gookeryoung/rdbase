"""审计日志模型测试."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from apps.accounts.models import Role, User
from apps.audit.models import (
    AuditAction,
    AuditLog,
    AuditSource,
    AuditStatus,
)

# ---------- 枚举（无需 DB） ----------


def test_audit_action_choices() -> None:
    """AuditAction 枚举应包含全部预期值."""
    assert AuditAction.WRITE == "write"
    assert AuditAction.LOGIN == "login"
    assert AuditAction.LOGOUT == "logout"
    assert AuditAction.DATASOURCE_CREATE == "datasource.create"
    assert AuditAction.DATASOURCE_UPDATE == "datasource.update"
    assert AuditAction.DATASOURCE_DELETE == "datasource.delete"
    assert AuditAction.DRAFT_CREATE == "draft.create"
    assert AuditAction.DRAFT_UPDATE == "draft.update"
    assert AuditAction.DRAFT_DELETE == "draft.delete"
    assert AuditAction.DRAFT_ROLLBACK == "draft.rollback"
    assert AuditAction.DDL_APPLY == "ddl.apply"
    assert AuditAction.DML_INSERT == "dml.insert"
    assert AuditAction.DML_UPDATE == "dml.update"
    assert AuditAction.DML_DELETE == "dml.delete"
    assert AuditAction.DML_IMPORT == "dml.import"
    assert AuditAction.SQL_EXECUTE == "sql.execute"
    assert AuditAction.OBJ_ALTER == "obj.alter"
    assert AuditAction.OBJ_DROP == "obj.drop"
    assert AuditAction.BACKUP_CREATE == "backup.create"
    assert AuditAction.BACKUP_RESTORE == "backup.restore"
    assert AuditAction.AUDIT_VERIFY == "audit.verify"
    assert AuditAction.DATASOURCE_SCAN == "datasource.scan"
    assert AuditAction.DATASET_CREATE == "dataset.create"
    assert AuditAction.DATASET_UPDATE == "dataset.update"
    assert AuditAction.DATASET_DELETE == "dataset.delete"
    assert AuditAction.DATASET_WRITE == "dataset.write"
    assert AuditAction.TOKEN_CREATE == "token.create"
    assert AuditAction.TOKEN_REVOKE == "token.revoke"
    assert AuditAction.TOKEN_ROTATE == "token.rotate"
    assert AuditAction.SYNC_TRIGGER == "sync.trigger"
    assert AuditAction.INGEST_TRIGGER == "ingest.trigger"
    assert AuditAction.WEBHOOK_DELIVER == "webhook.deliver"


def test_audit_source_choices() -> None:
    """AuditSource 枚举应有 middleware/business 两项."""
    assert AuditSource.MIDDLEWARE == "middleware"
    assert AuditSource.BUSINESS == "business"


def test_audit_status_choices() -> None:
    """AuditStatus 枚举应有 success/failure 两项."""
    assert AuditStatus.SUCCESS == "success"
    assert AuditStatus.FAILURE == "failure"


def test_audit_action_choices_count() -> None:
    """AuditAction 共 33 个枚举值."""
    assert len(AuditAction.choices) == 33


def test_audit_log_meta_ordering() -> None:
    """Meta.ordering 应为 -id（最新在前）."""
    assert AuditLog._meta.ordering == ["-id"]  # type: ignore[missing-attribute]


def test_audit_log_indexes() -> None:
    """模型应包含 5 个索引（user/action/datasource/created_at/status）."""
    index_names = {idx.name for idx in AuditLog._meta.indexes}  # type: ignore[missing-attribute]
    assert index_names == {
        "idx_audit_user",
        "idx_audit_action",
        "idx_audit_ds",
        "idx_audit_created",
        "idx_audit_status",
    }


def test_audit_log_max_lengths() -> None:
    """字符字段长度约束符合预期."""
    fields = {f.name: f for f in AuditLog._meta.get_fields() if hasattr(f, "max_length")}  # type: ignore[missing-attribute]
    assert fields["username"].max_length == 150
    assert fields["action"].max_length == 32
    assert fields["source"].max_length == 16
    assert fields["status"].max_length == 16
    assert fields["method"].max_length == 8
    assert fields["path"].max_length == 512
    assert fields["resource_type"].max_length == 64
    assert fields["resource_id"].max_length == 128
    assert fields["datasource_name"].max_length == 128
    assert fields["user_agent"].max_length == 512


# ---------- 实例与默认值（需 DB） ----------


@pytest.mark.django_db
def test_audit_log_defaults() -> None:
    """AuditLog 默认值：action=WRITE, source=MIDDLEWARE, status=SUCCESS, 空字符串字段."""
    log = AuditLog.objects.create()
    assert log.action == AuditAction.WRITE
    assert log.source == AuditSource.MIDDLEWARE
    assert log.status == AuditStatus.SUCCESS
    assert log.username == ""
    assert log.method == ""
    assert log.path == ""
    assert log.resource_type == ""
    assert log.resource_id == ""
    assert log.datasource_name == ""
    assert log.sql == ""
    assert log.error_message == ""
    assert log.user_agent == ""
    assert log.extra == {}
    assert log.user is None
    assert log.user_id is None
    assert log.datasource_id is None
    assert log.row_count is None
    assert log.elapsed_ms is None
    assert log.ip is None
    assert log.created_at is not None


@pytest.mark.django_db
def test_audit_log_str_with_username() -> None:
    """__str__ 应包含时间、用户名、动作、路径."""
    log = AuditLog.objects.create(
        username="alice",
        action=AuditAction.DML_INSERT,
        path="/api/v1/manager/ds/1/rows",
    )
    s = str(log)
    assert "alice" in s
    assert "dml.insert" in s
    assert "/api/v1/manager/ds/1/rows" in s


@pytest.mark.django_db
def test_audit_log_str_anonymous() -> None:
    """无用户名时 __str__ 应显示 '匿名'."""
    log = AuditLog.objects.create(
        username="",
        action=AuditAction.WRITE,
        path="/api/v1/datasources",
    )
    s = str(log)
    assert "匿名" in s


@pytest.mark.django_db
def test_audit_log_with_user_relation(make_user: Callable[..., User]) -> None:
    """通过 user 外键关联时仍使用 username 字段渲染."""
    user = make_user(username="bob", role=Role.ADMIN)
    log = AuditLog.objects.create(user=user, username=user.username)
    assert log.user_id == user.pk
    assert "bob" in str(log)


@pytest.mark.django_db
def test_audit_log_cascade_set_null_on_user_delete(
    make_user: Callable[..., User],
) -> None:
    """user 删除时 AuditLog.user 应置 NULL（on_delete=SET_NULL）."""
    user = make_user(username="carol", role=Role.ADMIN)
    log = AuditLog.objects.create(user=user, username=user.username)
    user.delete()
    log.refresh_from_db()
    assert log.user_id is None
    # username 冗余字段保留
    assert log.username == "carol"


@pytest.mark.django_db
def test_audit_log_extra_field_persists() -> None:
    """extra JSONField 应能存储任意 dict."""
    log = AuditLog.objects.create(extra={"file": "a.csv", "rows": 100})
    log.refresh_from_db()
    assert log.extra == {"file": "a.csv", "rows": 100}


# ---------- Admin 权限 ----------


def test_audit_admin_has_add_permission_false() -> None:
    """Admin 不允许手动创建审计日志."""
    from apps.audit.admin import AuditLogAdmin

    admin_instance = AuditLogAdmin(AuditLog, None)  # type: ignore[arg-type]
    assert admin_instance.has_add_permission(None) is False


def test_audit_admin_has_change_permission_false() -> None:
    """Admin 不允许修改审计日志."""
    from apps.audit.admin import AuditLogAdmin

    admin_instance = AuditLogAdmin(AuditLog, None)  # type: ignore[arg-type]
    assert admin_instance.has_change_permission(None) is False
