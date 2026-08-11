# Generated for P8-Q4 收集增强

from django.db import migrations, models


class Migration(migrations.Migration):
    """IngestTask 新增 webhook_token 与 incremental_config 字段（P8-Q4）.

    - webhook_token：仅 source_type=WEBHOOK 时使用，POST /ingest/webhook/{token} 路径鉴权。
    - incremental_config：驱动增量爬取策略（API updated_at / HTML 指纹 / DB timestamp_field）。
    """

    dependencies = [
        ("ingest", "0004_ingest_log_quality_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingesttask",
            name="incremental_config",
            field=models.JSONField(blank=True, default=dict, verbose_name="增量策略配置"),
        ),
        migrations.AddField(
            model_name="ingesttask",
            name="webhook_token",
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                unique=True,
                verbose_name="Webhook 接收 token",
            ),
        ),
        migrations.AlterField(
            model_name="ingesttask",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("api", "REST/JSON API"),
                    ("html", "网页 HTML"),
                    ("file", "文件下载"),
                    ("rss", "RSS/Atom"),
                    ("database", "数据库直连"),
                    ("webhook", "Webhook 被动接收"),
                ],
                max_length=20,
                verbose_name="源类型",
            ),
        ),
    ]
