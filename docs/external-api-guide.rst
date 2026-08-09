外部应用接入指南
================

本指南面向需要通过 API 接入 rdbase 数据中心能力的外部应用开发者。涵盖 API
Token 获取、鉴权请求头、数据集查询/写入、调度触发、Webhook 事件订阅与签名
校验等完整流程。

.. contents::
   :local:
   :depth: 2

概述
====

rdbase 在 Web 前端（JWT 会话）之外，提供 **API Token** 认证机制供外部应用
接入。Token 通过 ``X-API-Token`` 请求头传递，按 scope 粒度授权访问以下能力：

- ``datasets:read`` —— 只读查询数据集行。
- ``datasets:write`` —— 写入数据集行（单行/批量 UPSERT）。
- ``sync:trigger`` —— 触发数据集同步配置执行 / 爬取任务执行。

所有对外端点：

================================ =========================================
端点                              说明
================================ =========================================
``GET /api/v1/datasets/{slug}/rows`` 查询数据集行（分页/排序/筛选/字段裁剪）
``POST /api/v1/datasets/{slug}/rows`` 写入数据集行（单行/批量 UPSERT）
``POST /api/v1/datasets/{slug}/sync`` 触发数据集绑定的同步配置执行
``POST /api/v1/ingest/tasks/{id}/trigger`` 触发爬取任务执行
================================ =========================================

OpenAPI 规范：``GET /api/v1/datasets/openapi.json`` 返回外部视图，仅含上述
端点（不暴露管理端点，避免信息泄露）。

获取 API Token
==============

API Token 由管理员在 Web 控制台「API Token」页面创建。创建时返回的明文
Token **仅此一次展示**，请立即安全保存（DB 仅存 SHA-256 哈希，无法找回）。

请求示例（管理员通过 JWT 创建）：

.. code-block:: http

   POST /api/v1/tokens HTTP/1.1
   Authorization: Bearer <admin-jwt>
   Content-Type: application/json

   {
     "name": "external-app-readonly",
     "scopes": ["datasets:read", "sync:trigger"],
     "expires_at": null
   }

响应：

.. code-block:: json

   {
     "id": 1,
     "name": "external-app-readonly",
     "token": "rdbase_xxxxxxxxxxxxxxxxxxxxxxxx",
     "prefix": "rdbase_x",
     "scopes": ["datasets:read", "sync:trigger"],
     "expires_at": null,
     "is_active": true,
     "created_at": "2026-08-09T10:00:00Z"
   }

注意：

- Token 明文以 ``rdbase_`` 前缀开头，便于识别。
- ``expires_at`` 为 ``null`` 表示永久有效；建议按需设置过期时间。
- 列表/详情接口不返回明文，仅返回 ``prefix``（前 8 位）用于识别。
- 吊销后将 ``is_active`` 置 False，DB 记录保留用于审计；轮换生成新明文并
  覆盖哈希，旧明文立即失效。

鉴权请求头
==========

所有对外端点通过 ``X-API-Token`` 请求头鉴权：

.. code-block:: http

   GET /api/v1/datasets/users/rows HTTP/1.1
   X-API-Token: rdbase_xxxxxxxxxxxxxxxxxxxxxxxx

鉴权失败响应：

- ``401 Unauthorized`` —— Token 不存在 / 已吊销 / 已过期。
- ``403 Forbidden`` —— Token 缺少所需 scope（响应体含 ``Token 缺少 scope: xxx``）。

数据集查询
==========

通过数据集 ``slug``（如 ``users``）查询数据，不感知底层数据源与表名。

请求示例：

.. code-block:: http

   GET /api/v1/datasets/users/rows?page=1&page_size=20&order_by=id&order_dir=asc&columns=id,name&filters=%7B%22status%22%3A%22active%22%7D HTTP/1.1
   X-API-Token: rdbase_xxx

Query 参数：

- ``page``：页码，从 1 开始，默认 1。
- ``page_size``：每页行数，默认 20。
- ``order_by``：排序字段（须为表内列名）。
- ``order_dir``：排序方向 ``asc`` / ``desc``，默认 ``asc``。
- ``columns``：逗号分隔的列名列表；为空时按 ``fields_whitelist`` 或全部列返回。
- ``filters``：JSON 字符串，格式 ``{"列名": {"op": "eq|ne|gt|lt|ge|le|like|in", "val": ...}}``。
  与数据集 ``filter_expression`` AND 组合，同名列以数据集配置为准（防绕过）。

响应：

.. code-block:: json

   {
     "items": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
     "total": 2,
     "page": 1,
     "page_size": 20,
     "columns": ["id", "name"]
   }

行级过滤与列级权限：

- 数据集 ``filter_expression`` 强制行级过滤，外部无法绕过。
- 数据集 ``fields_whitelist`` 非空时，请求 ``columns`` 必须是其子集，否则 400。
- ``is_active=False`` 的数据集返回 404。

数据集写入
==========

支持单行/批量 UPSERT，冲突策略复用 ``ConflictStrategy``：

.. code-block:: http

   POST /api/v1/datasets/users/rows HTTP/1.1
   X-API-Token: rdbase_xxx
   Idempotency-Key: client-unique-key-123
   Content-Type: application/json

   {
     "rows": [
       {"id": 100, "name": "NewUser", "email": "new@example.com"},
       {"id": 1, "name": "AliceUpdated"}
     ],
     "conflict_strategy": "upsert",
     "pk_fields": ["id"]
   }

参数说明：

- ``rows``：行数据列表，每项为 ``列名 -> 值`` 的 dict；非空，单批 <= 1000 行。
- ``conflict_strategy``：``upsert``（默认）/ ``skip`` / ``error``。
- ``pk_fields``：主键字段名列表；为空时由反射自动推断；无主键且策略非 ``error``
  时返回 400。

响应：

.. code-block:: json

   {
     "written": 2,
     "skipped": 0,
     "total": 2
   }

保护机制：

- **幂等**：携带 ``Idempotency-Key`` 头，24h 内重复请求返回首次结果。
- **速率限制**：每 Token 每分钟 ``RATE_LIMIT_DATASET_WRITE``（默认 60）次写入
  请求，超限返回 ``429`` + ``Retry-After`` 头（令牌桶算法，支持突发与逐步恢复）。
- **每日配额**：每 Token 每日写入总行数上限 ``DATASET_WRITE_DAILY_QUOTA``
  （默认 10000），超限返回 ``429``。
- **列级写权限**：``fields_whitelist`` 非空时 rows 所有键必须是其子集。
- **审计**：每次写入记 ``DATASET_WRITE`` 审计日志（含 Token prefix、行数、耗时）。

触发同步
========

按数据集 ``slug`` 触发绑定的同步配置执行（异步）：

.. code-block:: http

   POST /api/v1/datasets/users/sync HTTP/1.1
   X-API-Token: rdbase_xxx
   Idempotency-Key: sync-key-456

响应（202 Accepted）：

.. code-block:: json

   {
     "task_id": "a1b2c3d4e5f6...",
     "sync_config_id": 1,
     "status": "accepted"
   }

特性：

- **异步执行**：立即返回 ``task_id``，调用方通过 ``SyncLog`` 查询执行结果
  （``GET /api/v1/sync/logs?config_id=1``）。
- **幂等 + 分布式锁**：``Idempotency-Key`` 24h 缓存；同配置并发触发返回 409。
- 数据集须绑定 ``sync_config``（否则 400）；同步配置须 ``is_active``（否则 400）。

触发爬取
========

按爬取任务 ID 触发执行（同步等待返回）：

.. code-block:: http

   POST /api/v1/ingest/tasks/42/trigger HTTP/1.1
   X-API-Token: rdbase_xxx
   Idempotency-Key: ingest-key-789

响应（200 OK）：

.. code-block:: json

   {
     "task_id": 42,
     "returncode": 0,
     "log": {
       "id": 100,
       "task_id": 42,
       "status": "success",
       "rows_read": 5,
       "rows_written": 5,
       "rows_skipped": 0,
       "duration_ms": 1234
     },
     "stderr": ""
   }

特性：

- **同步等待**：子进程执行完毕后返回 returncode 与最新日志。
- **幂等 + 分布式锁**：同任务并发触发返回 409（与内部 ``/run`` 同锁名互斥）。
- 失败返回 500 + FAILURE 审计日志。

Webhook 事件订阅
================

订阅 ``sync.completed`` / ``ingest.completed`` 事件，rdbase 在事件完成后
异步 POST 到订阅 URL，携带 HMAC-SHA256 签名头供接收方校验完整性。

订阅由管理员在 Web 控制台「Webhook 订阅」页面配置（URL/secret/events）。
投递请求格式：

.. code-block:: http

   POST <target_url> HTTP/1.1
   Content-Type: application/json
   X-Webhook-Event: sync.completed
   X-Webhook-Signature: sha256=<hmac-hex>

   {
     "config_id": 1,
     "log_id": 100,
     "status": "success",
     "mode": "full",
     "rows_read": 100,
     "rows_written": 100,
     "rows_skipped": 0,
     "duration_ms": 1234
   }

签名校验（Python 示例）：

.. code-block:: python

   import hashlib
   import hmac

   def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
       """校验 X-Webhook-Signature 头.

       Args:
           secret: 订阅时配置的 secret。
           body: 原始请求体字节（未解码）。
           signature_header: ``X-Webhook-Signature`` 头值，格式 ``sha256=<hex>``。

       Returns:
           校验通过返回 True，否则 False。
       """
       if not signature_header.startswith("sha256="):
           return False
       expected = signature_header.removeprefix("sha256=")
       computed = hmac.new(
           secret.encode("utf-8"),
           body,
           hashlib.sha256,
       ).hexdigest()
       return hmac.compare_digest(expected, computed)

   # Flask 示例
   # @app.route("/webhook", methods=["POST"])
   # def webhook():
   #     sig = request.headers.get("X-Webhook-Signature", "")
   #     if not verify_signature(WEBHOOK_SECRET, request.get_data(), sig):
   #         return "invalid signature", 401
   #     payload = request.get_json()
   #     # 处理事件...
   #     return "", 200

投递策略：

- **重试**：非 2xx 响应或网络异常时按指数退避 1/2/4/8/16s 重试，最多 5 次。
- **响应判定**：2xx 视为成功不再重试；其余状态码触发重试。
- **超时**：单次请求 10 秒。
- **日志**：每次投递流程（含全部重试）写一条 ``WebhookDeliveryLog``，可在
  Web 控制台查看 status_code、retry_count、duration_ms、error_message。

错误码汇总
==========

==== ==============================================================
401  Token 不存在 / 已吊销 / 已过期 / 无 Token
403  Token 缺少所需 scope / JWT 访问公开端点
404  数据集/数据源不存在或未启用；爬取任务不存在
400  入参非法（rows 空、列不存在、冲突策略非法、数据集未绑定 sync_config 等）
409  分布式锁占用（任务执行中）/ 幂等命中 in_progress
429  速率限制 / 每日配额超限（响应头含 ``Retry-After``）
500  子进程执行失败（爬取触发）
503  熔断器短路 / 健康检查不通过
==== ==============================================================

最佳实践
========

1. **Token 命名清晰**：``<应用名>-<scope>-<环境>``，如 ``bi-reporter-read-prod``。
2. **按需授权 scope**：只读应用不要给 ``write`` / ``trigger`` scope。
3. **设置过期时间**：定期轮换降低泄露风险；泄露后立即吊销。
4. **使用 Idempotency-Key**：所有写操作和触发操作都应携带，避免网络重试导致
   重复写入或重复触发。
5. **妥善保存明文**：DB 仅存哈希，明文丢失只能吊销重建。
6. **Webhook 校验签名**：永远校验 ``X-Webhook-Signature``，防伪造投递。
7. **处理 429**：尊重 ``Retry-After`` 头，指数退避重试，避免雪崩。
8. **监控 last_used_at**：管理员可在 Token 管理页查看最近使用时间，及时
   清理闲置 Token。
