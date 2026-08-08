# Generated for iter-43：新增 DATASET_WRITE 审计动作

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0004_alter_auditlog_action"),
    ]

    operations = [
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
                    ("dataset.create", "创建数据集"),
                    ("dataset.update", "更新数据集"),
                    ("dataset.delete", "删除数据集"),
                    ("dataset.write", "写入数据集"),
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
                    ("token.create", "创建 Token"),
                    ("token.revoke", "吊销 Token"),
                    ("token.rotate", "轮换 Token"),
                ],
                default="write",
                max_length=32,
                verbose_name="操作类型",
            ),
        ),
    ]
