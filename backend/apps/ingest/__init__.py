"""数据摄取应用包.

提供从外部源（REST/JSON API、网页 HTML、文件下载、RSS/Atom）爬取数据
并写入已配置数据源的能力。基于 Scrapy 引擎，子进程隔离运行。
"""

default_app_config = "apps.ingest.apps.IngestConfig"
