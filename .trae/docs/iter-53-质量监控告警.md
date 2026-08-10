# iter-53：P8-Q3 质量监控告警

## 需求清单

- [x] 44 IngestLog 新增 quality_score 字段（0-100），爬取完成后写入。
- [x] 45 质量告警（rows_invalid/rows_total > 阈值时 WARNING）+ 字段健康度统计。
- [x] 46 监控面板扩展（质量分卡片 + 字段健康度表格）。

## 迭代目标

在 P8-Q2 质量校验基础上引入质量监控告警能力：

1. IngestLog 新增 `quality_score` FloatField（0-100，默认 100）。
2. ValidationPipeline.close_spider 按校验通过率加权计算质量分写回 IngestLog，
   并按可配置阈值（warning=80/critical=60）产生 IngestAlert（WARNING/ERROR）。
3. IngestQualityReport 新增 `field_health` 类方法：按 (field, rule) 聚合历史质量
   数据，返回平均通过率/总检查数/失败数/最近一次通过率，最差字段在前。
4. IngestLog.aggregate_stats 增加 avg_quality_score；监控面板 stats 端点同步返回。
5. 新增 `GET /ingest/field-health` 与 `GET /ingest/tasks/{id}/field-health` 端点。
6. 前端监控面板加"平均质量分"卡片 + "字段健康度"表格；日志表格加"质量分"列。

## 改动文件清单

### 修改

- `backend/apps/ingest/models.py`：
  - IngestLog 新增 `quality_score` FloatField（default=100.0）
  - IngestStats dataclass 新增 `avg_quality_score` 字段
  - IngestLog.aggregate_stats 聚合 avg_quality_score（Avg("quality_score")）
  - IngestQualityReport 新增 `field_health` 类方法（按 field+rule 聚合历史报告，
    recent 限制样本数，按平均通过率升序返回）
- `backend/apps/ingest/validation.py`：
  - 模块级新增 `DEFAULT_WARNING_THRESHOLD`/`DEFAULT_CRITICAL_THRESHOLD` 常量
  - 新增 `_coerce_threshold` 辅助函数（容错解析阈值，越界截断到 0-100）
  - ValidationPipeline.__init__ 加 `_warning_threshold`/`_critical_threshold` 字段
  - open_spider 读取 validation_config.quality_thresholds（非 dict/非法值容错）
  - 新增 `_compute_quality_score` 方法（_passed_checks/_total_checks * 100）
  - 新增 `_maybe_raise_quality_alert` 方法（按阈值产生 IngestAlert）
  - close_spider 计算质量分 → 写入 stats（ingest_quality_score）→ 写回
    IngestLog.quality_score → 阈值告警（仅在有规则时触发）
- `backend/apps/ingest/schemas.py`：
  - IngestLogOut 加 `quality_score: float = 100.0`
  - IngestStatsOut 加 `avg_quality_score: float = 0.0`
  - 新增 IngestFieldHealthOut schema
- `backend/apps/ingest/api.py`：
  - 导入 IngestFieldHealthOut
  - _log_to_out 加 quality_score
  - get_stats 返回 avg_quality_score
  - 新增 `GET /ingest/field-health`（?task_id, ?recent 上限 100）
  - 新增 `GET /ingest/tasks/{task_id}/field-health`
- `frontend/src/types/index.ts`：
  - IngestLog 加 `quality_score: number`
  - IngestStats 加 `avg_quality_score: number`
  - 新增 IngestFieldHealth 接口
- `frontend/src/api/ingest.ts`：
  - 新增 getIngestFieldHealth / getIngestTaskFieldHealth
- `frontend/src/pages/Ingest.tsx`：
  - 导入 getIngestFieldHealth 与 IngestFieldHealth 类型
  - 新增 fieldHealth state，loadMonitor 并行加载
  - logColumns 加"质量分"列（Tag 颜色按 90/70 阈值）
  - 新增 fieldHealthColumns（8 列：字段/规则/平均通过率/最近通过率/检查次数/
    失败次数/样本数/最近报告）
  - 监控面板统计行加"平均质量分"卡片（替代"累计跳过行"位置，原卡片移到下行）
  - 监控面板加"字段健康度"表格区（位于失败告警上方）

### 新增

- `backend/apps/ingest/migrations/0004_ingest_log_quality_score.py`：IngestLog
  新增 quality_score 字段 migration。
- `tests/test_ingest_quality_monitoring.py`：47 用例（_coerce_threshold 9 +
  _compute_quality_score 4 + _maybe_raise_quality_alert 7 + close_spider 6 +
  IngestLog.quality_score 4 + field_health 5 + API 12）。
- `.trae/docs/iter-53-质量监控告警.md`：本迭代记录。

### 删除

- `.trae/docs/iter-48-Webhook重投功能.md`（迭代文件数达 6，按规则清理最旧 iter-48，
  保留最新 5 条）。

## 关键决策与依据

1. **质量分计算口径**：用 `_passed_checks / _total_checks * 100`（校验次数加权通过率），
   而非按规则数平均。理由：单条 item 触发多条规则时，按次数加权更准确反映数据质量；
   无规则时为 100（视为全部通过，避免无校验任务被误判低分）。
2. **阈值告警复用 IngestAlert**：不新建表，level=ERROR 对应 CRITICAL，
   level=WARNING 对应 WARNING。复用现有 raise_alert 类方法与监控面板告警表格。
3. **阈值可配置**：从 `validation_config.quality_thresholds` 读取
   `{warning: 80, critical: 60}`，非法值（非 dict/非数字）回退到默认；
   超出 0-100 截断到边界。
4. **告警仅在有规则时触发**：避免无校验规则任务（quality_score=100）产生噪声告警。
5. **field_health 内存聚合**：监控场景报告数有限，按 (field, rule) 分组到内存
   计算，避免 N 次 SQL 查询；recent 限制每条 (field, rule) 取最近 N 条报告
   参与统计（默认 10，上限 100），API 层做上限保护防止恶意传参拖慢查询。
6. **quality_score 默认 100**：现有 IngestLog 历史数据无此字段，默认 100
   避免历史日志被误判为低质量；新执行由 ValidationPipeline 写入实际值。
7. **avg_quality_score 用 Avg 聚合**：与 avg_duration_ms 同口径，无日志时为 0
   （与 success_rate 语义一致）。
8. **stats 配置结构**：
   ```json
   {
     "rules": [...],
     "quality_thresholds": {"warning": 80, "critical": 60}
   }
   ```

## 代码实现情况

- `validation.py`：
  - `_coerce_threshold(value, default)`：None/非数字回退默认；负数→0；>100→100
  - `ValidationPipeline._compute_quality_score()`：无校验时 100；否则
    `round(passed / total * 100, 1)`
  - `ValidationPipeline._maybe_raise_quality_alert(task, score)`：< critical →
    ERROR 告警；< warning → WARNING 告警；否则不告警
  - `ValidationPipeline.close_spider()`：计算质量分 → 写 stats → 写
    IngestLog.quality_score（update_fields）→ bulk_create 报告 → 有规则时
    触发阈值告警
- `models.py`：
  - `IngestQualityReport.field_health(task_id=None, recent=10)`：按 (field, rule)
    分组取最近 recent 条报告，计算 avg_pass_rate/total_checks/total_failures/
    last_pass_rate/samples，按 avg_pass_rate 升序返回
- `api.py`：
  - `GET /ingest/field-health`：可选 ?task_id/N 与 ?recent/N（1-100，默认 10）
  - `GET /ingest/tasks/{id}/field-health`：任务级字段健康度，404 任务不存在

## 整合优化情况

- `_coerce_threshold` 抽取为独立函数（而非内联在 open_spider），便于单测覆盖
  各类非法输入。
- close_spider 中 `log.save(update_fields=["quality_score"])` 仅更新单字段，
  避免与并发写入冲突。
- field_health 用 `values()` 一次查询取全部所需字段到内存，避免 N+1 查询。

## 测试验证结果

### 单元测试（test_ingest_quality_monitoring.py，47 用例全部通过）

```
uv run pytest tests/test_ingest_quality_monitoring.py
47 passed in 3.57s
```

覆盖：
- `_coerce_threshold` 9 用例（int/float/str 数字/str 非法/None/负数/>100/
  tuple/list）
- `_compute_quality_score` 4 用例（无校验/全通过/半通过/四舍五入）
- `_maybe_raise_quality_alert` 7 用例（无告警/WARNING/CRITICAL/自定义阈值/
  非法阈值回退/非 dict 忽略/越界截断）
- close_spider 6 用例（写 quality_score/无规则写 100/WARNING 告警/CRITICAL 告警/
  无告警/无 task_id 跳过）
- IngestLog.quality_score 4 用例（默认值/无日志 stats/有日志 stats/task 过滤）
- field_health 5 用例（空/单任务/任务过滤/recent 限制/字段规则分组）
- API 12 用例（field-health 空/有数据/task 过滤/非法 task_id/recent 参数/
  recent 上限/下限/任务级/404；stats 含 avg_quality_score；logs 含 quality_score）

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 239 files already formatted
uv run pyrefly check                          # 0 errors (262 suppressed)
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1864 passed, 15 deselected, 54 warnings in 86.34s
  TOTAL 8901 stmts, 319 miss, 1784 branch, 124 brpart, 95.55%
```

覆盖率 95.55%（≥95% 门禁），较 iter-52 的 95.49% 提升 0.06%，新增 47 用例未拉低
整体。测试数 1864（较 1817 新增 47）。

### 前端

```
cd frontend && bun run typecheck   # tsc --noEmit 通过
```

## 遗留事项

- P8-Q4 收集增强：DATABASE 直连源 + Webhook 被动接收 + 增量策略扩展。
- P8-Q5 文档与测试：覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例。

## 下一轮计划

- iter-54：P8-Q4 收集增强 — DATABASE 直连源 + Webhook 被动接收 + 增量策略扩展。
