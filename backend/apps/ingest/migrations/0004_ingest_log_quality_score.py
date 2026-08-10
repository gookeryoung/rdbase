# Generated for P8-Q3 质量监控告警

from django.db import migrations, models


class Migration(migrations.Migration):
    """IngestLog 新增 quality_score 字段（0-100，由 ValidationPipeline 写入）."""

    dependencies = [
        ("ingest", "0003_ingestqualityreport"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestlog",
            name="quality_score",
            field=models.FloatField(default=100.0, verbose_name="质量分（0-100）"),
        ),
    ]
