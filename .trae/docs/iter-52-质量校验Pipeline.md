# iter-52：P8-Q2 质量校验

## 需求清单

- [x] 41 ValidationPipeline + 6 类规则（必填/范围/正则/枚举/唯一/自定义表达式）。
- [x] 42 IngestQualityReport 模型（任务/字段/规则/通过率/失败样本）+ API + 前端质量报告页。
- [x] 43 Pipeline 顺序固定为 Spider → CleaningPipeline(200) → ValidationPipeline(250) →
  FieldMappingPipeline(300)。

## 迭代目标

为 ingest 模块引入数据质量校验能力。清洗后的 item 在进入字段映射前先经
ValidationPipeline 校验：按 validation_config 配置执行 6 类规则（required/range/regex/
enum/unique/expression）。校验失败不丢弃 item（与 CleaningPipeline DropItem 不同），
仅记录失败样本到 IngestQualityReport 并累加 stats。空配置透传不破坏既有行为。

## 改动文件清单

### 修改

- `backend/apps/ingest/models.py`：新增 IngestQualityReport 模型（task/log FK + field/rule/
  total/passed/failed/pass_rate/failure_samples + aggregate_summary 类方法 + 2 索引）。
- `backend/apps/ingest/engine.py`：`_build_scrapy_settings` 注册 ValidationPipeline(250)；
  `_build_spider_kwargs` 注入 validation_config。
- `backend/apps/ingest/spiders/base.py`：BaseIngestSpider 新增 validation_config 属性；
  from_task 工厂同步注入。
- `backend/apps/ingest/api.py`：新增 _quality_report_to_out + list_task_quality_reports +
  get_task_quality_summary 两个端点；导入 IngestQualityReport 与两个新 schema。
- `backend/apps/ingest/schemas.py`：新增 IngestQualityReportOut / IngestQualitySummaryOut。
- `frontend/src/types/index.ts`：新增 IngestQualityReport / IngestQualitySummary 接口。
- `frontend/src/api/ingest.ts`：新增 listIngestQualityReports / getIngestQualitySummary。
- `frontend/src/pages/Ingest.tsx`：新增 SafetyCertificateOutlined 图标 + 质量报告状态 +
  loadQuality/handleViewQuality + 质量报告 Modal（4 统计卡 + 表格）+ qualityReportColumns +
  校验配置填充模板（defaultValidationConfig）。

### 新增

- `backend/apps/ingest/validation.py`：ValidationPipeline + 6 类校验器 + _RuleStats 累加器。
- `backend/apps/ingest/migrations/0003_ingestqualityreport.py`：IngestQualityReport 表 migration。
- `tests/test_ingest_validation.py`：95 用例（6 类校验器 + _RuleStats + Pipeline 生命周期 +
  模型 + API）。
- `.trae/docs/iter-52-质量校验Pipeline.md`：本迭代记录。

### 删除

- `.trae/docs/iter-47-触发端点限流.md`（迭代文件数达 6，按规则清理最旧 iter-47，保留最新 5 条）。

## 关键决策与依据

1. **校验失败不丢弃 item**：与 CleaningPipeline 的 DropItem 不同，ValidationPipeline 仅
   记录失败样本与降低 quality_score（P8-Q3 实现），item 继续流向 FieldMappingPipeline
   写入目标表。理由：校验是质量度量而非数据过滤，避免因单条规则失败导致数据丢失。
2. **Pipeline 顺序数字**：ValidationPipeline 注册 250，位于 CleaningPipeline(200) 与
   FieldMappingPipeline(300) 之间。清洗后再校验，确保校验面对的是规范化后的数据。
3. **失败样本上限**：每条规则最多保留 MAX_SAMPLES_PER_RULE=20 条失败样本，避免大流量
   场景下 IngestQualityReport.failure_samples 膨胀。
4. **expression 安全求值**：用 eval 但限定 `__builtins__` 为安全子集（abs/len/min/max/
   round/sum/str/int/float/bool），禁用 __import__ 与危险属性访问；求值异常视为失败。
5. **unique 批次内去重**：用进程内 set 维护已见值（dict/list 转为可哈希键），仅单次执行
   内有效，不跨执行共享（与 DedupTracker 的 Redis 共享不同，校验场景无需跨执行）。
6. **IngestQualityReport 批次定义**：close_spider 时按 (field, rule) 聚合，关联到任务
   最近一次 IngestLog。aggregate_summary 取 latest 报告所属 log 的全部报告作为同一批。
7. **validation_config 结构**：
   ```json
   {
     "rules": [
       {"field": "name", "op": "required"},
       {"field": "age", "op": "range", "min": 0, "max": 150},
       {"field": "email", "op": "regex", "pattern": "^[^@]+@[^@]+$"},
       {"field": "status", "op": "enum", "values": ["active", "inactive"]},
       {"field": "id", "op": "unique"},
       {"field": "age", "op": "expression", "expr": "value > 0"}
     ]
   }
   ```
8. **stats 收集**：ingest_validation_total/passed/failed + ingest_rows_invalid（至少一条
   规则失败的 item 数，去重计数）。

## 代码实现情况

- `validation.py` 提供 6 类校验器 + _RuleStats 累加器 + ValidationPipeline：
  - `_validate_required`：空值视为失败
  - `_validate_range`：min/max 数值范围，非数值或越界失败
  - `_validate_regex`：正则匹配，非法正则容错为失败
  - `_validate_enum`：枚举值列表，支持 str/原始值比较
  - `_validate_unique`：批次内唯一性，dict/list 转可哈希键
  - `_validate_expression`：安全 eval，限定 __builtins__
  - `_RuleStats`：累计 total/passed/failed/samples，pass_rate 计算
  - `ValidationPipeline`：from_crawler 绑定 stats；open_spider 读 validation_config +
    task_id，初始化 _rule_stats 与 _unique_seen；process_item 调用 _evaluate_rule 执行
    单条规则，记录样本与 stats；close_spider 写 stats 总计 + 批量创建 IngestQualityReport
    关联到任务最近一次 IngestLog。
- `models.py` IngestQualityReport：task/log 双 FK（级联删除）+ field/rule/total_count/
  passed_count/failed_count/pass_rate/failure_samples(JSON) + 2 索引（task+created_at,
  log）+ aggregate_summary 类方法（取 latest log 的全部报告汇总）。
- `api.py` 两个新端点：
  - `GET /tasks/{id}/quality-reports`：列出质量报告，支持 ?log_id=N 过滤
  - `GET /tasks/{id}/quality-summary`：返回最近一批报告的汇总摘要
- 前端 Ingest.tsx：
  - 任务操作列加"查看质量报告"按钮（SafetyCertificateOutlined 图标）
  - 质量报告 Modal：4 统计卡（规则数/平均通过率/失败样本/最差字段）+ 报告表格
    （字段/规则/总数/通过/失败/通过率/失败样本预览/报告时间）
  - 校验配置区从"预留"改为正式启用，加"填充模板"按钮（defaultValidationConfig）

## 整合优化情况

- process_item 原始实现分支数 16 > 12（PLR0912），提取 `_evaluate_rule` 辅助方法后
  降至合规，且可读性更好。
- _validate_range 末尾 `if ... return False; return True` 简化为
  `return not (...)`（SIM103）。
- migration 0003 复用 IngestLog 的 task FK 模式，与 IngestAlert 风格一致。

## 测试验证结果

### 单元测试（test_ingest_validation.py，95 用例全部通过）

```
uv run pytest tests/test_ingest_validation.py --cov=apps.ingest.validation
95 passed in 2.89s
backend/apps/ingest/validation.py     214 3 90 2 98%
```

覆盖：
- 6 类校验器单独验证（required 7 / range 10 / regex 9 / enum 8 / unique 7 / expression 11）
- _RuleStats 累加器（初始状态/记录通过/记录失败/混合通过率/样本上限/复杂值序列化）
- ValidationPipeline 生命周期：空配置透传、规则按序执行、失败不丢弃、stats 收集、
  close_spider 写 IngestQualityReport、无 task_id/log/空规则跳过、校验器异常容错
- IngestQualityReport 模型：__str__/defaults/aggregate_summary/级联删除
- 质量报告 API：list（空/有数据/log_id 过滤/404/普通用户权限）、summary（空/有数据/404）

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 237 files already formatted
uv run pyrefly check                          # 0 errors (262 suppressed)
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1817 passed, 15 deselected, 54 warnings in 86.22s
  TOTAL 8804 stmts, 320 miss, 1762 branch, 124 brpart, 95.49%
```

覆盖率 95.49%（≥95% 门禁），较 iter-51 的 95.38% 提升 0.11%，新增 validation.py 98% 覆盖
未拉低整体。测试数 1817（较 1722 新增 95）。

### 前端

```
cd frontend && bun run typecheck   # tsc --noEmit 通过
```

## 遗留事项

- P8-Q3 质量监控告警：IngestLog.quality_score 字段 + 字段健康度统计 + 监控面板扩展。
- P8-Q4 收集增强：DATABASE 直连源 + Webhook 被动接收 + 增量策略扩展。
- P8-Q5 文档与测试：覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例。

## 下一轮计划

- iter-53：P8-Q3 质量监控告警 — IngestLog.quality_score + 字段健康度 + 监控面板扩展。
