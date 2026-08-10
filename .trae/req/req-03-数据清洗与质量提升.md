# 需求：数据清洗与质量提升（P8）

## 概述

在 P7 数据爬取模块基础上，引入「数据清洗 → 质量校验 → 质量监控告警 → 收集增强 →
文档与测试」五阶段闭环，提升入库数据质量与可观测性。复用 ingest 模块现有
Pipeline 机制（Scrapy ITEM_PIPELINES 数字控制顺序）与 IngestAlert/IngestLog 模式，
不重复造轮子。

## 定位

P8 是 P7 数据爬取的纵深增强：

- P8-Q1 清洗 Pipeline：Spider → CleaningPipeline → FieldMappingPipeline → Writer
- P8-Q2 质量校验：ValidationPipeline + IngestQualityReport 模型 + 前端质量报告页
- P8-Q3 质量监控告警：IngestLog.quality_score + 字段健康度 + 监控面板扩展
- P8-Q4 收集增强：DATABASE 直连源 + Webhook 被动接收 + 增量策略扩展
- P8-Q5 文档与测试：覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例

## 需求清单

### P8-Q1 清洗 Pipeline 基础（里程碑：清洗器可配置并按序执行）

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

### P8-Q2 质量校验

- [x] 41 ValidationPipeline + 6 类规则（必填/范围/正则/枚举/唯一/引用完整性/自定义表达式）。
- [x] 42 IngestQualityReport 模型（任务/字段/规则/通过率/失败样本）+ API + 前端质量报告页。
- [x] 43 Pipeline 顺序固定为 Spider → CleaningPipeline(200) → ValidationPipeline(250) →
  FieldMappingPipeline(300)。

### P8-Q3 质量监控告警

- [ ] 44 IngestLog 新增 quality_score 字段（0-100），爬取完成后写入。
- [ ] 45 质量告警（rows_invalid/rows_total > 阈值时 WARNING）+ 字段健康度统计。
- [ ] 46 监控面板扩展（质量分卡片 + 字段健康度表格）。

### P8-Q4 收集增强

- [ ] 47 SourceType.DATABASE 直连源（SQL 查询爬取器）+ Webhook 被动接收
  （POST /ingest/webhook/{token}）。
- [ ] 48 增量策略扩展（API 按 updated_at 参数 / HTML 按指纹 / DB 按 timestamp_field）。

### P8-Q5 文档与测试

- [ ] 49 覆盖率 ≥ 95% 回归 + README/手册同步 + 端到端用例。

## 关键架构决策

1. **配置驱动清洗**：clean_config 与 validation_config 用 JSONField，与 parse_config
   同级，前端任务编辑页加规则配置区。结构示例：
   ```json
   {
     "rules": [
       {"field": "name", "op": "on_missing", "strategy": "fill_default", "default": ""},
       {"field": "age", "op": "cast_type", "cast_type": "int"},
       {"field": "phone", "op": "normalize", "normalizer": "phone"},
       {"field": "status", "op": "enum_map", "mapping": {"1": "active"}}
     ],
     "dedup": {"enabled": true, "fields": ["id"], "ttl_hours": 24}
   }
   ```
2. **Pipeline 顺序数字控制**：Scrapy ITEM_PIPELINES 数字越小越先执行；
   CleaningPipeline(200) → ValidationPipeline(250) → FieldMappingPipeline(300)。
3. **去重 Redis 优先**：可用时用 Redis SET + SADD 判重；不可用时降级为内存 set
   （单进程内有效，多 worker 不共享，仅作开发兜底）。
4. **空配置透传**：clean_config 为空字典时 CleaningPipeline 直接透传 item，
   不影响现有 P7 任务的行为（向后兼容）。
5. **清洗器无副作用**：清洗器接收并返回 dict，不修改 spider 状态；统计通过
   crawler.stats 收集（ingest_rows_cleaned/ingest_rows_dropped）。

## 验收标准

1. `make check` 全套门禁通过（ruff + pyrefly + pytest，覆盖率 ≥ 95% 不下降）。
2. 清洗 Pipeline 各清洗器（缺失值/类型转换/格式标准化/去重/HTML 剥离/枚举映射）
   行为符合配置语义，集成测试覆盖典型场景。
3. 空清洗配置时行为与 P7 一致（向后兼容）。
4. 前端任务编辑页可配置清洗规则并正确提交后端。
5. 公共 API 有完整类型注解与中文 docstring。
6. 不重复造轮子，与 sync 模块风格保持一致。
