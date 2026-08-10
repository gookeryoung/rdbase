# Generated for P8-Q2 数据质量校验

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """新增 IngestQualityReport 模型（任务/日志/字段/规则/通过率/失败样本）."""

    dependencies = [
        ("ingest", "0002_ingest_task_clean_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="IngestQualityReport",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("field", models.CharField(max_length=128, verbose_name="字段名")),
                ("rule", models.CharField(max_length=32, verbose_name="规则类型")),
                (
                    "total_count",
                    models.PositiveIntegerField(default=0, verbose_name="样本总数"),
                ),
                (
                    "passed_count",
                    models.PositiveIntegerField(default=0, verbose_name="通过数"),
                ),
                (
                    "failed_count",
                    models.PositiveIntegerField(default=0, verbose_name="失败数"),
                ),
                (
                    "pass_rate",
                    models.FloatField(default=100.0, verbose_name="通过率（百分比）"),
                ),
                (
                    "failure_samples",
                    models.JSONField(
                        blank=True,
                        default=list,
                        verbose_name="失败样本",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quality_reports",
                        to="ingest.ingesttask",
                        verbose_name="爬取任务",
                    ),
                ),
                (
                    "log",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quality_reports",
                        to="ingest.ingestlog",
                        verbose_name="执行日志",
                    ),
                ),
            ],
            options={
                "verbose_name": "数据质量报告",
                "verbose_name_plural": "数据质量报告",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="ingestqualityreport",
            index=models.Index(fields=["task", "-created_at"], name="ingest_qr_task_idx"),
        ),
        migrations.AddIndex(
            model_name="ingestqualityreport",
            index=models.Index(fields=["log"], name="ingest_qr_log_idx"),
        ),
    ]
