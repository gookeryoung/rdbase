"""审计日志模型.

记录所有 DDL/DML 与管理类写操作的留痕信息，含用户、时间、SQL、影响行数等关键字段。
分两类来源：

- 中间件层（``AuditMiddleware``）：拦截所有 POST/PATCH/PUT/DELETE 请求，记录通用字段
  （用户/IP/方法/路径/状态码/耗时），``sql``/``row_count``/``datasource_id`` 等业务字段为空。
- 业务层（``log_audit`` 辅助函数）：在写操作 view 内显式调用，补充 SQL 文本、影响行数、
  数据源 ID、资源类型/ID 等业务上下文。

两类记录都通过 ``AuditLog`` 模型统一存储，通过 ``source`` 字段区分来源。
"""

from __future__ import annotations

from django.db import models

from apps.accounts.models import User


class AuditAction(models.TextChoices):
    """审计操作类型枚举."""

    # 通用写操作（中间件层捕获，未细化分类时使用）
    WRITE = "write", "写操作"
    # 认证类
    LOGIN = "login", "登录"
    LOGOUT = "logout", "登出"
    # 数据源管理
    DATASOURCE_CREATE = "datasource.create", "创建数据源"
    DATASOURCE_UPDATE = "datasource.update", "更新数据源"
    DATASOURCE_DELETE = "datasource.delete", "删除数据源"
    DATASOURCE_SCAN = "datasource.scan", "扫描数据源"
    # 设计器
    DRAFT_CREATE = "draft.create", "创建草稿"
    DRAFT_UPDATE = "draft.update", "更新草稿"
    DRAFT_DELETE = "draft.delete", "删除草稿"
    DRAFT_ROLLBACK = "draft.rollback", "回滚版本"
    DDL_APPLY = "ddl.apply", "应用 DDL"
    # 数据管理
    DML_INSERT = "dml.insert", "新增行"
    DML_UPDATE = "dml.update", "更新行"
    DML_DELETE = "dml.delete", "删除行"
    DML_IMPORT = "dml.import", "导入数据"
    SQL_EXECUTE = "sql.execute", "执行 SQL"
    # 对象管理
    OBJ_ALTER = "obj.alter", "编辑对象"
    OBJ_DROP = "obj.drop", "删除对象"


class AuditSource(models.TextChoices):
    """审计记录来源枚举."""

    MIDDLEWARE = "middleware", "中间件"
    BUSINESS = "business", "业务层"


class AuditStatus(models.TextChoices):
    """审计操作结果枚举."""

    SUCCESS = "success", "成功"
    FAILURE = "failure", "失败"


class AuditLog(models.Model):
    """审计日志条目.

    一条记录对应一次写操作或一次认证事件。中间件层与业务层共用此模型，
    通过 ``source`` 字段区分。业务层记录会补充 ``sql``/``row_count``/``resource_type`` 等字段。
    """

    # 显式声明 manager 供类型检查识别
    objects: models.Manager[AuditLog]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="操作用户",
    )
    username = models.CharField(max_length=150, blank=True, default="", verbose_name="用户名（冗余）")
    action = models.CharField(
        max_length=32,
        choices=AuditAction.choices,
        default=AuditAction.WRITE,
        verbose_name="操作类型",
    )
    source = models.CharField(
        max_length=16,
        choices=AuditSource.choices,
        default=AuditSource.MIDDLEWARE,
        verbose_name="记录来源",
    )
    status = models.CharField(
        max_length=16,
        choices=AuditStatus.choices,
        default=AuditStatus.SUCCESS,
        verbose_name="操作结果",
    )
    method = models.CharField(max_length=8, blank=True, default="", verbose_name="HTTP 方法")
    path = models.CharField(max_length=512, blank=True, default="", verbose_name="请求路径")
    # 业务上下文
    resource_type = models.CharField(max_length=64, blank=True, default="", verbose_name="资源类型")
    resource_id = models.CharField(max_length=128, blank=True, default="", verbose_name="资源 ID")
    datasource_id = models.IntegerField(null=True, blank=True, verbose_name="数据源 ID")
    datasource_name = models.CharField(max_length=128, blank=True, default="", verbose_name="数据源名称（冗余）")
    sql = models.TextField(blank=True, default="", verbose_name="SQL 文本")
    row_count = models.IntegerField(null=True, blank=True, verbose_name="影响行数")
    elapsed_ms = models.IntegerField(null=True, blank=True, verbose_name="耗时（毫秒）")
    # 客户端信息
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="客户端 IP")
    user_agent = models.CharField(max_length=512, blank=True, default="", verbose_name="User-Agent")
    # 失败时的错误信息
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    # 额外扩展字段（如导入文件名、对象类型等）
    extra = models.JSONField(default=dict, blank=True, verbose_name="扩展信息")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="操作时间")

    class Meta:
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["user"], name="idx_audit_user"),
            models.Index(fields=["action"], name="idx_audit_action"),
            models.Index(fields=["datasource_id"], name="idx_audit_ds"),
            models.Index(fields=["created_at"], name="idx_audit_created"),
            models.Index(fields=["status"], name="idx_audit_status"),
        ]

    def __str__(self) -> str:  # type: ignore[missing-override-decorator]
        """返回简要描述."""
        who = self.username or "匿名"
        return f"[{self.created_at:%Y-%m-%d %H:%M:%S}] {who} {self.action} {self.path}"  # type: ignore[bad-return]


__all__ = [
    "AuditAction",
    "AuditLog",
    "AuditSource",
    "AuditStatus",
]
