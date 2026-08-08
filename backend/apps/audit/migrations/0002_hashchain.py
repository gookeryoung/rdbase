"""新增哈希链字段并回填历史记录."""

from __future__ import annotations

from typing import Any

from django.db import migrations, models

from apps.audit.hashchain import compute_record_hash


def backfill_hashes(apps: Any, schema_editor: Any) -> None:  # noqa: ARG001
    """按 id 升序遍历历史记录，串行回填 prev_hash 与 record_hash."""
    AuditLog = apps.get_model("audit", "AuditLog")
    prev_hash = ""
    for record in AuditLog.objects.order_by("id").iterator():
        record.prev_hash = prev_hash
        record.record_hash = compute_record_hash(record, prev_hash)
        record.save(update_fields=["prev_hash", "record_hash"])
        prev_hash = record.record_hash


def clear_hashes(apps: Any, schema_editor: Any) -> None:  # noqa: ARG001
    """回滚：清空 prev_hash 与 record_hash."""
    AuditLog = apps.get_model("audit", "AuditLog")
    AuditLog.objects.update(prev_hash="", record_hash="")


class Migration(migrations.Migration):
    """新增 prev_hash/record_hash 字段 + 扩展 action choices + 回填历史哈希."""

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="prev_hash",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="前一条记录哈希"),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="record_hash",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="本条记录哈希"),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("write", "写操作"),
                    ("login", "登录"),
                    ("logout", "登出"),
                    ("datasource.create", "创建数据源"),
                    ("datasource.update", "更新数据源"),
                    ("datasource.delete", "删除数据源"),
                    ("datasource.scan", "扫描数据源"),
                    ("draft.create", "创建草稿"),
                    ("draft.update", "更新草稿"),
                    ("draft.delete", "删除草稿"),
                    ("draft.rollback", "回滚版本"),
                    ("ddl.apply", "应用 DDL"),
                    ("dml.insert", "新增行"),
                    ("dml.update", "更新行"),
                    ("dml.delete", "删除行"),
                    ("dml.import", "导入数据"),
                    ("sql.execute", "执行 SQL"),
                    ("obj.alter", "编辑对象"),
                    ("obj.drop", "删除对象"),
                    ("backup.create", "创建备份"),
                    ("backup.restore", "恢复备份"),
                    ("audit.verify", "审计校验"),
                ],
                default="write",
                max_length=32,
                verbose_name="操作类型",
            ),
        ),
        migrations.RunPython(backfill_hashes, clear_hashes),
    ]
