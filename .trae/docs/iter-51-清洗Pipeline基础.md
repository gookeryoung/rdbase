# iter-51：P8-Q1 清洗 Pipeline 基础

## 需求清单

- [ ] 36 IngestTask 新增 `clean_config` 与 `validation_config` 两个 JSONField
  （与 parse_config 同级，default=dict, blank=True），加 migration 0002。
- [ ] 37 CleaningPipeline 实现：按 clean_config 配置执行
  - 缺失值处理（on_missing: skip/fill_default/abort）
  - 类型转换（cast_type: int/float/bool/datetime/json）
  - 格式标准化（normalizer: trim/upper/lower/phone/email/url/date）
  - 去重（行级哈希指纹 + Redis SET，Redis 不可用时降级为内存 set）
  - HTML 剥离
  - 枚举映射
- [ ] 38 Scrapy settings ITEM_PIPELINES 注册：CleaningPipeline(200) → FieldMappingPipeline(300)，
  清洗后再映射写入；空 clean_config 时透传不影响现有任务。
- [ ] 39 前端任务编辑页加清洗规则配置区（结构化表单 + JSON 预览），类型定义与 API 同步更新。
- [ ] 40 测试补全：cleaning pipeline 各清洗器单测 + 集成测试，覆盖率 ≥ 95% 不下降。

## 迭代目标

为 ingest 模块引入清洗 Pipeline 基础能力。Spider 产出的原始 item 在进入字段映射前
先经 CleaningPipeline 处理：按 clean_config 配置执行缺失值、类型转换、格式标准化、
去重、HTML 剥离、枚举映射等清洗操作。空配置透传不破坏 P7 既有行为。

## 改动文件清单

### 修改

- `backend/apps/ingest/models.py`：IngestTask 新增 clean_config / validation_config 两个 JSONField。
- `backend/apps/ingest/engine.py`：`_build_scrapy_settings` 注册 CleaningPipeline(200)；
  `_build_spider_kwargs` 与 `_build_spider_settings` 传入 clean_config；
  base spider 接收 clean_config 参数。
- `backend/apps/ingest/spiders/base.py`：BaseIngestSpider 新增 clean_config 属性。
- `backend/apps/ingest/api.py`：`_task_to_out` / create / update 同步 clean_config/validation_config；
  IngestTaskCreateIn / IngestTaskUpdateIn / IngestTaskOut 同步字段。
- `backend/apps/ingest/schemas.py`：3 个 schema 新增 clean_config / validation_config 字段。
- `frontend/src/types/index.ts`：IngestTask / IngestTaskCreate 新增两个字段。
- `frontend/src/pages/Ingest.tsx`：任务编辑页加清洗规则配置区（JSON 编辑器 + 模板按钮）。
- `backend/apps/ingest/migrations/0002_ingest_task_clean_config.py`：新增字段 migration。

### 新增

- `backend/apps/ingest/cleaning.py`：CleaningPipeline + 6 类清洗器实现 + DedupTracker。
- `tests/test_ingest_cleaning.py`：清洗器单测 + 集成测试。
- `.trae/docs/iter-51-清洗Pipeline基础.md`：本迭代记录。

### 删除

- 无（迭代文件数当前为 5，新增后达 6，按规则清理最旧 iter-46）。

## 关键决策与依据

1. **Pipeline 顺序数字控制**：Scrapy ITEM_PIPELINES 数字越小越先执行。设定
   CleaningPipeline(200) → FieldMappingPipeline(300)，留出 250 给后续 ValidationPipeline。
2. **空配置透传**：clean_config 为空字典时 CleaningPipeline 不修改 item，直接返回原 item。
   保证 P7 既有任务行为不变（向后兼容）。
3. **去重 Redis 优先 + 内存降级**：`DedupTracker` 优先用 Redis SET + SADD 判重；
   Redis 不可用时降级为进程内 set（仅单进程有效，多 worker 不共享）。
   指纹用 SHA-256 哈希指定字段值。
4. **清洗器无副作用**：每个清洗器接收 item 与 rule 配置，返回处理后的 item 或 None
   （表示应丢弃）。统计通过 crawler.stats 收集（ingest_rows_cleaned/ingest_rows_dropped）。
5. **clean_config 结构**：
   ```json
   {
     "rules": [
       {"field": "name", "op": "on_missing", "strategy": "fill_default", "default": ""},
       {"field": "age", "op": "cast_type", "cast_type": "int"},
       {"field": "phone", "op": "normalize", "normalizer": "phone"},
       {"field": "status", "op": "enum_map", "mapping": {"1": "active"}},
       {"field": "desc", "op": "strip_html"}
     ],
     "dedup": {"enabled": true, "fields": ["id"], "ttl_hours": 24}
   }
   ```
6. **前端表单策略**：清洗规则用 JSON 编辑器（TextArea + 校验）+ 预置模板按钮，
   与现有 HTML fields 配置风格一致，避免为 6 类规则各写一行表单导致界面臃肿。

## 代码实现情况

详见各文件实现。核心实现要点：

- `cleaning.py` 提供 `CleaningPipeline` 类与 6 个清洗器函数：
  - `_apply_on_missing`：skip 丢弃 / fill_default 填默认值 / abort 中止整批
  - `_apply_cast_type`：int/float/bool/datetime/json 五种类型转换
  - `_apply_normalize`：trim/upper/lower/phone/email/url/date 七种标准化
  - `_apply_strip_html`：剥离 HTML 标签
  - `_apply_enum_map`：枚举值映射
  - `DedupTracker`：行级哈希指纹 + Redis SET / 内存 set 判重
- `engine.py` 在 `_build_scrapy_settings` 中按顺序注册两个 pipeline；
  `_build_spider_kwargs` 注入 clean_config；spider 通过属性传递给 pipeline。
- `cleaning.py` 的 `CleaningPipeline.from_crawler` 绑定 stats；
  `open_spider` 时从 spider 读取 clean_config 与任务 ID（用于去重 key 命名空间）；
  `process_item` 按规则依次清洗，丢弃的 item 抛 `DropItem` 让 Scrapy 统计计入 dropped；
  `close_spider` 写入 stats。

## 整合优化情况

无新重复代码。清洗器函数均为纯函数（除 DedupTracker 有状态）。
CleaningPipeline 与 FieldMappingPipeline 风格一致（from_crawler + open/process/close）。

## 测试验证结果

### 单元测试（test_ingest_cleaning.py，83 用例全部通过）

```
uv run pytest tests/test_ingest_cleaning.py --cov=apps.ingest.cleaning
83 passed in 0.31s
backend/apps/ingest/cleaning.py     223 0 102 0 100%
```

覆盖：
- 6 类清洗器单独验证（on_missing 3 策略 / cast_type 5 类型 + 失败 / normalize 7 标准化 /
  strip_html / enum_map + 默认值）
- DedupTracker 内存模式（5 用例）+ fakeredis 模式（4 用例）
- CleaningPipeline 生命周期：空配置透传、规则按序执行、DropItem 传播、stats 收集、
  close_spider 兜底、from_crawler 绑定
- 集成测试：CleaningPipeline + FieldMappingPipeline 协作（清洗后字段正确映射写入目标表）

### 全套门禁

```
uv run ruff check backend tests              # All checks passed!
uv run ruff format --check backend tests     # 234 files already formatted
uv run pyrefly check                          # 0 errors (254 suppressed)
uv run pytest -m "not slow" --cov=backend --cov-fail-under=95
  1722 passed, 15 deselected, 54 warnings in 85.47s
  TOTAL 8518 stmts, 317 miss, 1670 branch, 122 brpart, 95.38%
```

覆盖率 95.38%（≥95% 门禁），与 iter-50 持平，新增 cleaning.py 100% 覆盖未拉低整体。

### 前端

```
cd frontend && bun run typecheck   # tsc --noEmit 通过
```

## 遗留事项

- P8-Q2 ValidationPipeline + IngestQualityReport 模型（下一迭代）。
- P8-Q3 quality_score + 字段健康度（Q3）。
- P8-Q4 DATABASE 直连源 + Webhook（Q4）。
- P8-Q5 文档与端到端用例（Q5）。

## 下一轮计划

- iter-52：P8-Q2 质量校验 — ValidationPipeline + 6 类规则 + IngestQualityReport 模型 + 前端质量报告页。
