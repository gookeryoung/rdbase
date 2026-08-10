# iter-48：WebhookDeliveryLog 重投功能

## 需求清单

- [x] 48 WebhookDeliveryLog 重投功能：iter-44/45 遗留事项，投递失败后支持手动
  重投与调度自动重投，形成「内联重试 → 调度重投」两级恢复链。

## 迭代目标

为 Webhook 投递失败场景补齐重投能力：

1. **deliverer 层**：`_deliver_one` 投递失败时设 `next_retry_at` 标记待调度重投；
   新增 `redeliver(log_id)` 按源日志 ID 重新投递。
2. **API 层**：新增 `POST /webhooks/{sub_id}/deliveries/{log_id}/redeliver` 端点。
3. **管理命令**：`retry_failed_webhooks` 扫描到期日志批量重投。
4. **前端**：投递日志表格加「重投」按钮（仅非 2xx 行显示）。

## 改动文件清单

### 修改

- `backend/apps/webhook/deliverer.py`：
  - import 补充 `datetime`/`timedelta`/`WebhookDeliveryLog`。
  - 新增 `_SCHEDULED_RETRY_INTERVAL = 300`（5 分钟）常量。
  - `_deliver_one` 返回类型改为 `WebhookDeliveryLog | None`；finally 中投递失败
    时设 `next_retry_at = now + _SCHEDULED_RETRY_INTERVAL`；成功时为 None；
    返回创建的日志。
  - 新增 `redeliver(log_id)` 函数：读源日志 → 清其 `next_retry_at` →
    在独立线程同步调 `_deliver_one`（避免 `connections.close_all()` 关闭调用方
    线程的 DB 连接）→ 返回新日志。
  - `__all__` 导出 `redeliver`。
  - 模块文档补充重投与 `next_retry_at` 说明。
- `backend/apps/webhook/api.py`：
  - import 补充 `from .deliverer import redeliver`。
  - 新增 `POST /{sub_id}/deliveries/{log_id}/redeliver` 端点：校验订阅存在 +
    校验日志归属该订阅 → 调 `redeliver(log_id)` → 记审计日志 → 返回新日志 Out。
  - 模块文档补充重投端点。
- `frontend/src/api/webhooks.ts`：新增 `redeliverWebhookDelivery(subId, logId)`。
- `frontend/src/pages/Webhooks.tsx`：
  - import 补充 `RedoOutlined` + `redeliverWebhookDelivery`。
  - 新增 `redeliveringId` 状态追踪重投中的日志 ID。
  - 新增 `refreshDeliveries`（重投后刷新列表）与 `handleRedeliver` 处理函数。
  - 投递日志表格末尾新增「操作」列，仅非 2xx 行显示「重投」按钮 +
    Popconfirm 确认 + loading 状态。

### 新增

- `backend/apps/webhook/management/__init__.py`
- `backend/apps/webhook/management/commands/__init__.py`
- `backend/apps/webhook/management/commands/retry_failed_webhooks.py`：
  `BaseCommand`，扫描 `next_retry_at IS NOT NULL AND next_retry_at <= now()`
  的日志，逐条调 `redeliver(log.pk)`，输出摘要（扫描N条、成功M条、失败K条）。
- `tests/test_webhook_commands.py`：管理命令测试（4 个用例）。

### 测试补充

- `tests/test_webhook_deliverer.py`：新增 7 个用例（next_retry_at 成功/失败/
  网络异常 + redeliver 创建新日志/清源 next_retry_at/不存在/订阅删除/失败循环）。
- `tests/test_webhook_api.py`：新增 `TestWebhookRedeliverAPI` 类（6 个用例：
  成功/订阅不存在/日志不存在/跨订阅/权限/未认证）。

## 关键决策与依据

1. **重投创建新 DeliveryLog**：每次重投（手动/调度）用原始 event_type+payload
   调 `_deliver_one`，创建新日志，原日志保留作审计。链式自然终止（成功不设
   `next_retry_at`，调度器不再扫描）。

2. **`_deliver_one` 设 `next_retry_at`**：内联重试全部失败后（status 非 2xx），
   设 `next_retry_at = now + 300s`（5 分钟），标记待调度重投。成功时保持 None。
   网络异常（status_code=None）同样设 `next_retry_at`。

3. **`redeliver` 在独立线程执行 `_deliver_one`**：`_deliver_one` 的 finally 调
   `connections.close_all()` 关闭当前线程的 DB 连接。若在 API 请求线程/管理命令
   线程直接调用，会关闭调用方的连接。用独立线程 + join 隔离，确保调用方线程
   连接不受影响，同时保持「同步阻塞等待结果」语义。

4. **`redeliver` 清源日志 `next_retry_at`**：避免调度器重复重投同一源日志。
   清除在重投前执行，即使新投递也失败，新日志会带新的 `next_retry_at`，
   调度器下次扫描新日志即可。

5. **API 端点校验日志归属**：URL 含 `sub_id` 与 `log_id`，端点校验日志属于
   该订阅（`filter(pk=log_id, subscription_id=sub_id).exists()`），不匹配返回 404。
   `redeliver` 函数本身只按 `log_id` 查找，不校验订阅归属（调度器无订阅上下文）。

6. **管理命令扫描策略**：`next_retry_at IS NOT NULL AND next_retry_at <= now()`，
   一次性加载全部到期日志到内存（`list(queryset)`），避免迭代中 `redeliver` 修改
   `next_retry_at` 影响查询。逐条调 `redeliver`，统计成功/失败数。

7. **前端仅非 2xx 行显示重投按钮**：成功投递不需要重投；`status_code` 为 null
   或非 2xx 范围才显示。Popconfirm 确认 + loading 状态防重复点击。

## 代码实现情况

### deliverer 核心改动

```python
_SCHEDULED_RETRY_INTERVAL = 300  # 调度重投间隔（秒）

def _deliver_one(...) -> WebhookDeliveryLog | None:
    # ... 投递逻辑不变 ...
    finally:
        next_retry_at = None
        if not _is_success(last_status):
            next_retry_at = timezone.now() + timedelta(seconds=_SCHEDULED_RETRY_INTERVAL)
        created_log = WebhookDeliveryLog.objects.create(..., next_retry_at=next_retry_at)
    return created_log

def redeliver(log_id: int) -> WebhookDeliveryLog | None:
    source = WebhookDeliveryLog.objects.get(pk=log_id)  # 不存在返回 None
    if source.next_retry_at is not None:
        source.next_retry_at = None
        source.save(update_fields=["next_retry_at"])
    # 独立线程执行，隔离 connections.close_all()
    result: list[WebhookDeliveryLog | None] = [None]
    def _run(): result[0] = _deliver_one(sub_id, event_type, payload_copy)
    thread = threading.Thread(target=_run, daemon=True)
    thread.start(); thread.join()
    return result[0]
```

### API 端点

```python
@router.post("/{sub_id}/deliveries/{log_id}/redeliver", response={200: WebhookDeliveryLogOut})
def redeliver_delivery(request, sub_id, log_id):
    require_admin(request)
    _get_sub_or_404(sub_id)
    if not WebhookDeliveryLog.objects.filter(pk=log_id, subscription_id=sub_id).exists():
        raise HttpError(404, ...)
    new_log = redeliver(log_id)
    if new_log is None:
        raise HttpError(404, ...)
    log_audit(request, action=AuditAction.WEBHOOK_DELIVER, ...)
    return JsonResponse(_log_to_out(new_log).model_dump(mode="json"))
```

### 管理命令

```python
class Command(BaseCommand):
    def handle(self, *args, **options):
        now = timezone.now()
        pending = list(WebhookDeliveryLog.objects.filter(
            next_retry_at__isnull=False, next_retry_at__lte=now))
        if not pending:
            self.stdout.write("无到期的待调度 Webhook 重投任务")
            return
        succeeded = failed = 0
        for log in pending:
            if redeliver(log.pk) is not None:
                succeeded += 1
            else:
                failed += 1
        self.stdout.write(self.style.SUCCESS(
            f"Webhook 重投完成：扫描 {len(pending)} 条，成功重投 {succeeded} 条，失败 {failed} 条"))
```

## 测试验证结果

- `uv run ruff check backend tests`：All checks passed。
- `uv run ruff format --check backend tests`：231 files already formatted。
- `uv run pyrefly check`：0 errors（248 suppressed, 1036 warnings not shown）。
- `uv run pytest -m "not slow" --cov=backend --cov-fail-under=95`：
  1630 passed, 15 deselected，覆盖率 95.19%（≥ 95% 阈值通过）。
- 前端 `bun run typecheck`（tsc --noEmit）：通过。

### 测试覆盖明细

- deliverer：21 个用例（原 14 + 新 7），覆盖 next_retry_at 成功/失败/网络异常、
  redeliver 创建新日志/清源标记/不存在/订阅删除/失败循环。
- API：26 个用例（原 20 + 新 6），覆盖重投成功/订阅不存在/日志不存在/
  跨订阅/权限拒绝/未认证。
- 管理命令：4 个用例，覆盖无到期/批量重投/未来时间跳过/null 跳过。

## 遗留事项

- iter-45 遗留的「API Token 按数据集细粒度授权」「审计哈希链定时校验」
  等待用户复核是否推进。
- 重投间隔（`_SCHEDULED_RETRY_INTERVAL = 300s`）为硬编码常量，未提供动态
  配置能力；后续若需差异化可扩展 SystemSetting。
- 管理命令需外部定时器（cron/任务计划）周期调用，未集成 Django celery/crontab。

## 下一轮计划

本期 WebhookDeliveryLog 重投功能收尾完成，无下一轮计划。如需推进 iter-45
遗留的对外 API 增强方向（Token 细粒度授权、审计哈希链校验），待用户确认后
启动新迭代。
