import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  DashboardOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  GlobalOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";

import {
  ackIngestAlert,
  createIngestTask,
  deleteIngestTask,
  getIngestFieldHealth,
  getIngestStats,
  getIngestQualitySummary,
  listIngestAlerts,
  listIngestQualityReports,
  listIngestTaskLogs,
  listIngestTasks,
  runIngestTask,
  updateIngestTask,
} from "@/api/ingest";
import { listDatasources } from "@/api/datasources";
import type {
  AlertLevel,
  DataSource,
  IngestAlert,
  IngestAuthType,
  IngestConflictStrategy,
  IngestFieldHealth,
  IngestFieldMapping,
  IngestLog,
  IngestLogStatus,
  IngestQualityReport,
  IngestQualitySummary,
  IngestRunResult,
  IngestSourceType,
  IngestStats,
  IngestTask,
  IngestTaskCreate,
} from "@/types";

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

// 状态标签颜色
const statusColor: Record<string, string> = {
  active: "success",
  paused: "default",
  error: "error",
};

// 源类型标签
const sourceTypeLabel: Record<IngestSourceType, string> = {
  api: "API",
  html: "HTML",
  file: "文件",
  rss: "RSS",
};

// 冲突策略标签
const conflictLabel: Record<IngestConflictStrategy, string> = {
  upsert: "更新",
  skip: "跳过",
  error: "报错",
};

// 日志状态标签
const logStatusLabel: Record<IngestLogStatus, string> = {
  success: "成功",
  partial: "部分",
  failed: "失败",
};

const logStatusColor: Record<IngestLogStatus, string> = {
  success: "success",
  partial: "warning",
  failed: "error",
};

const emptyMapping = (): IngestFieldMapping => ({
  source_field: "",
  target_field: "",
  mapping_type: "direct",
  fixed_value: "",
  is_pk: false,
});

// 默认解析配置（按源类型）
const defaultParseConfig = (sourceType: IngestSourceType): Record<string, unknown> => {
  switch (sourceType) {
    case "api":
      return { items_path: "", next_page_path: "", next_page_max: 0 };
    case "html":
      return {
        selector_type: "css",
        container_selector: "",
        fields: {},
        next_page_selector: "",
        next_page_attr: "href",
        next_page_max: 0,
      };
    case "file":
      return { format: "csv", encoding: "utf-8", delimiter: ",", sheet: "", items_path: "" };
    case "rss":
      return { include_feed_metadata: false };
  }
};

// 默认请求配置
const defaultRequestConfig = (): Record<string, unknown> => ({
  concurrent_requests: 8,
  timeout: 30,
  download_delay: 0,
  user_agent: "rdbase-ingest/1.0",
  cookies_enabled: false,
});

const createEmptyTask = (): IngestTaskCreate => ({
  name: "",
  description: "",
  source_type: "api",
  source_url: "",
  parse_config: defaultParseConfig("api"),
  request_config: defaultRequestConfig(),
  headers: {},
  auth_type: "none",
  target_datasource_id: 0,
  target_table: "",
  conflict_strategy: "upsert",
  batch_size: 500,
  obey_robots: true,
  scheduler_enabled: false,
  cron_expression: "",
  clean_config: {},
  validation_config: {},
  field_mappings: [emptyMapping()],
});

// 清洗配置默认模板（点击「填充模板」时使用）
const defaultCleanConfig = (): Record<string, unknown> => ({
  rules: [
    { field: "name", op: "on_missing", strategy: "fill_default", default: "" },
    { field: "age", op: "cast_type", cast_type: "int" },
    { field: "phone", op: "normalize", normalizer: "phone" },
  ],
  dedup: { enabled: false, fields: [], ttl_hours: 24 },
});

// 校验配置默认模板（点击「填充模板」时使用，P8-Q2）
const defaultValidationConfig = (): Record<string, unknown> => ({
  rules: [
    { field: "name", op: "required" },
    { field: "age", op: "range", min: 0, max: 150 },
    { field: "email", op: "regex", pattern: "^[^@]+@[^@]+$" },
    { field: "status", op: "enum", values: ["active", "inactive"] },
    { field: "id", op: "unique" },
    { field: "age", op: "expression", expr: "value > 0" },
  ],
});

export default function IngestPage() {
  const [tasks, setTasks] = useState<IngestTask[]>([]);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [logs, setLogs] = useState<IngestLog[]>([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [viewingLogsTaskId, setViewingLogsTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  // 创建/编辑对话框
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<IngestTaskCreate | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingHasHeaders, setEditingHasHeaders] = useState(false);
  const [headersDraft, setHeadersDraft] = useState<{ key: string; value: string }[]>([]);
  const [fieldsDraftJson, setFieldsDraftJson] = useState("");
  const [fieldsJsonError, setFieldsJsonError] = useState<string>("");
  const [cleanConfigDraftJson, setCleanConfigDraftJson] = useState("");
  const [cleanConfigJsonError, setCleanConfigJsonError] = useState<string>("");
  const [validationConfigDraftJson, setValidationConfigDraftJson] = useState("");
  const [validationConfigJsonError, setValidationConfigJsonError] = useState<string>("");
  const [saving, setSaving] = useState(false);

  // 执行结果对话框
  const [runResult, setRunResult] = useState<IngestRunResult | null>(null);
  const [runResultOpen, setRunResultOpen] = useState(false);
  const [running, setRunning] = useState(false);

  // 监控面板
  const [monitorOpen, setMonitorOpen] = useState(false);
  const [stats, setStats] = useState<IngestStats | null>(null);
  const [alerts, setAlerts] = useState<IngestAlert[]>([]);
  const [unackCount, setUnackCount] = useState(0);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [statsDays, setStatsDays] = useState<number | null>(7);
  const [onlyUnacked, setOnlyUnacked] = useState(true);
  // 字段健康度（P8-Q3）
  const [fieldHealth, setFieldHealth] = useState<IngestFieldHealth[]>([]);

  // 质量报告（P8-Q2）
  const [qualityReports, setQualityReports] = useState<IngestQualityReport[]>([]);
  const [qualitySummary, setQualitySummary] = useState<IngestQualitySummary | null>(null);
  const [qualityOpen, setQualityOpen] = useState(false);
  const [viewingQualityTaskId, setViewingQualityTaskId] = useState<number | null>(null);
  const [qualityLoading, setQualityLoading] = useState(false);

  // --- 数据加载 ---
  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listIngestTasks();
      setTasks(data);
    } catch {
      message.error("加载爬取任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDatasources = useCallback(async () => {
    try {
      const data = await listDatasources();
      setDatasources(data);
    } catch {
      // 数据源接口失败静默
    }
  }, []);

  const loadLogs = useCallback(async (taskId?: number) => {
    if (!taskId) return;
    try {
      const data = await listIngestTaskLogs(taskId);
      setLogs(data);
    } catch {
      message.error("加载爬取日志失败");
    }
  }, []);

  const loadUnackCount = useCallback(async () => {
    try {
      const data = await listIngestAlerts(false);
      setUnackCount(data.length);
    } catch {
      // 徽标辅助信息
    }
  }, []);

  const loadQuality = useCallback(async (taskId?: number) => {
    if (!taskId) return;
    setQualityLoading(true);
    try {
      const [reports, summary] = await Promise.all([
        listIngestQualityReports(taskId),
        getIngestQualitySummary(taskId),
      ]);
      setQualityReports(reports);
      setQualitySummary(summary);
    } catch {
      message.error("加载质量报告失败");
    } finally {
      setQualityLoading(false);
    }
  }, []);

  const loadMonitor = useCallback(async () => {
    setMonitorLoading(true);
    try {
      const [statsData, alertData, fieldHealthData] = await Promise.all([
        getIngestStats(statsDays ?? undefined),
        listIngestAlerts(!onlyUnacked),
        getIngestFieldHealth(undefined, 10),
      ]);
      setStats(statsData);
      setAlerts(alertData);
      setFieldHealth(fieldHealthData);
      setUnackCount(onlyUnacked ? alertData.length : alertData.filter((a) => !a.acknowledged).length);
    } catch {
      message.error("加载监控数据失败");
    } finally {
      setMonitorLoading(false);
    }
  }, [statsDays, onlyUnacked]);

  useEffect(() => {
    loadTasks();
    loadDatasources();
    loadUnackCount();
  }, [loadTasks, loadDatasources, loadUnackCount]);

  useEffect(() => {
    if (monitorOpen) loadMonitor();
  }, [monitorOpen, loadMonitor]);

  // --- 创建/编辑 ---
  const handleOpenCreate = () => {
    const empty = createEmptyTask();
    setEditingTask(empty);
    setEditingId(null);
    setEditingHasHeaders(false);
    setHeadersDraft([]);
    setFieldsDraftJson("{}");
    setFieldsJsonError("");
    setCleanConfigDraftJson("{}");
    setCleanConfigJsonError("");
    setValidationConfigDraftJson("{}");
    setValidationConfigJsonError("");
    setDialogOpen(true);
  };

  const handleOpenEdit = (task: IngestTask) => {
    const draft: IngestTaskCreate = {
      name: task.name,
      description: task.description,
      source_type: task.source_type,
      source_url: task.source_url,
      parse_config: { ...task.parse_config },
      request_config: { ...task.request_config },
      headers: {},
      auth_type: task.auth_type,
      target_datasource_id: task.target_datasource_id,
      target_table: task.target_table,
      conflict_strategy: task.conflict_strategy,
      batch_size: task.batch_size,
      obey_robots: task.obey_robots,
      scheduler_enabled: task.scheduler_enabled,
      cron_expression: task.cron_expression,
      clean_config: { ...task.clean_config },
      validation_config: { ...task.validation_config },
      field_mappings: task.field_mappings.map((m) => ({
        source_field: m.source_field,
        target_field: m.target_field,
        mapping_type: m.mapping_type,
        fixed_value: m.fixed_value,
        is_pk: m.is_pk,
      })),
    };
    setEditingTask(draft);
    setEditingId(task.id);
    setEditingHasHeaders(task.has_headers);
    setHeadersDraft([]);
    const fieldsCfg = (task.parse_config?.fields as Record<string, unknown> | undefined) ?? {};
    setFieldsDraftJson(JSON.stringify(fieldsCfg, null, 2));
    setFieldsJsonError("");
    setCleanConfigDraftJson(JSON.stringify(task.clean_config ?? {}, null, 2));
    setCleanConfigJsonError("");
    setValidationConfigDraftJson(JSON.stringify(task.validation_config ?? {}, null, 2));
    setValidationConfigJsonError("");
    setDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setDialogOpen(false);
    setEditingTask(null);
    setEditingId(null);
  };

  const handleSourceTypeChange = (v: IngestSourceType) => {
    if (!editingTask) return;
    const newParse = defaultParseConfig(v);
    setEditingTask({ ...editingTask, source_type: v, parse_config: newParse });
    if (v === "html") {
      setFieldsDraftJson("{}");
    }
  };

  // 解析配置字段更新（标量字段）
  const updateParseField = (key: string, value: unknown) => {
    if (!editingTask) return;
    setEditingTask({
      ...editingTask,
      parse_config: { ...editingTask.parse_config, [key]: value },
    });
  };

  // 请求配置字段更新
  const updateRequestField = (key: string, value: unknown) => {
    if (!editingTask) return;
    setEditingTask({
      ...editingTask,
      request_config: { ...editingTask.request_config, [key]: value },
    });
  };

  // 字段映射增删改
  const addMapping = () => {
    if (!editingTask) return;
    setEditingTask({
      ...editingTask,
      field_mappings: [...editingTask.field_mappings, emptyMapping()],
    });
  };

  const removeMapping = (index: number) => {
    if (!editingTask) return;
    const mappings = [...editingTask.field_mappings];
    mappings.splice(index, 1);
    setEditingTask({ ...editingTask, field_mappings: mappings });
  };

  const updateMapping = (index: number, updates: Partial<IngestFieldMapping>) => {
    if (!editingTask) return;
    const mappings = [...editingTask.field_mappings];
    mappings[index] = { ...mappings[index], ...updates };
    setEditingTask({ ...editingTask, field_mappings: mappings });
  };

  // 请求头编辑
  const addHeader = () => {
    setHeadersDraft([...headersDraft, { key: "", value: "" }]);
  };

  const removeHeader = (index: number) => {
    const next = [...headersDraft];
    next.splice(index, 1);
    setHeadersDraft(next);
  };

  const updateHeader = (index: number, field: "key" | "value", value: string) => {
    const next = [...headersDraft];
    next[index] = { ...next[index], [field]: value };
    setHeadersDraft(next);
  };

  const handleSave = async () => {
    if (!editingTask) return;
    if (!editingTask.name.trim()) {
      message.warning("请填写任务名称");
      return;
    }
    if (!editingTask.source_url.trim()) {
      message.warning("请填写源 URL");
      return;
    }
    if (!editingTask.target_datasource_id) {
      message.warning("请选择目标数据源");
      return;
    }
    if (!editingTask.target_table.trim()) {
      message.warning("请填写目标表名");
      return;
    }
    const validMappings = editingTask.field_mappings.filter(
      (m) => m.source_field.trim() || m.mapping_type === "constant"
    );
    if (validMappings.length === 0) {
      message.warning("请添加至少一个有效字段映射");
      return;
    }
    if (editingTask.scheduler_enabled && !editingTask.cron_expression?.trim()) {
      message.warning("启用调度时须填写 Cron 表达式");
      return;
    }

    // HTML fields 配置校验
    let finalParseConfig = { ...editingTask.parse_config };
    if (editingTask.source_type === "html") {
      try {
        const parsed = JSON.parse(fieldsDraftJson || "{}");
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setFieldsJsonError("fields 必须是 JSON 对象");
          return;
        }
        finalParseConfig = { ...finalParseConfig, fields: parsed };
        setFieldsJsonError("");
      } catch (err) {
        setFieldsJsonError(err instanceof Error ? err.message : "JSON 解析失败");
        return;
      }
    }

    // 清洗配置 JSON 校验
    let finalCleanConfig: Record<string, unknown> = { ...editingTask.clean_config };
    try {
      const parsed = JSON.parse(cleanConfigDraftJson || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setCleanConfigJsonError("清洗配置必须是 JSON 对象");
        return;
      }
      finalCleanConfig = parsed as Record<string, unknown>;
      setCleanConfigJsonError("");
    } catch (err) {
      setCleanConfigJsonError(err instanceof Error ? err.message : "JSON 解析失败");
      return;
    }

    // 校验配置 JSON 校验（P8-Q2 启用，当前仅校验格式）
    let finalValidationConfig: Record<string, unknown> = {
      ...editingTask.validation_config,
    };
    try {
      const parsed = JSON.parse(validationConfigDraftJson || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setValidationConfigJsonError("校验配置必须是 JSON 对象");
        return;
      }
      finalValidationConfig = parsed as Record<string, unknown>;
      setValidationConfigJsonError("");
    } catch (err) {
      setValidationConfigJsonError(err instanceof Error ? err.message : "JSON 解析失败");
      return;
    }

    // 构造 headers（仅保留非空 key 的项）
    const headerEntries = headersDraft
      .filter((h) => h.key.trim())
      .map((h) => [h.key.trim(), h.value] as [string, string]);
    const headers: Record<string, string> = {};
    for (const [k, v] of headerEntries) headers[k] = v;
    const hasNewHeaders = Object.keys(headers).length > 0;
    // 编辑模式下若未填新 headers，不传 headers 字段，后端保留原值
    const shouldSendHeaders = !editingId || hasNewHeaders;

    setSaving(true);
    try {
      const payload: IngestTaskCreate = {
        ...editingTask,
        parse_config: finalParseConfig,
        clean_config: finalCleanConfig,
        validation_config: finalValidationConfig,
        field_mappings: validMappings,
        headers: shouldSendHeaders ? headers : undefined,
      };
      if (editingId) {
        await updateIngestTask(editingId, payload);
        message.success("爬取任务已更新");
      } else {
        await createIngestTask(payload);
        message.success("爬取任务已创建");
      }
      handleCloseDialog();
      loadTasks();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteIngestTask(id);
      message.success("爬取任务已删除");
      loadTasks();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleRun = async (id: number) => {
    setRunning(true);
    setRunResultOpen(true);
    setRunResult(null);
    try {
      const result = await runIngestTask(id);
      setRunResult(result);
      if (result.returncode === 0 && result.log?.status === "success") {
        message.success(
          `爬取成功：读取 ${result.log.rows_read}，写入 ${result.log.rows_written}，跳过 ${result.log.rows_skipped}`
        );
      } else if (result.returncode === 0 && result.log?.status === "partial") {
        message.warning(
          `部分成功：读取 ${result.log.rows_read}，写入 ${result.log.rows_written}，跳过 ${result.log.rows_skipped}`
        );
      } else {
        message.error(`爬取失败（returncode=${result.returncode}）`);
      }
      loadTasks();
      loadUnackCount();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "爬取触发失败");
      setRunResultOpen(false);
    } finally {
      setRunning(false);
    }
  };

  const handleViewLogs = (taskId: number) => {
    setViewingLogsTaskId(taskId);
    loadLogs(taskId);
    setLogsOpen(true);
  };

  const handleViewQuality = (taskId: number) => {
    setViewingQualityTaskId(taskId);
    setQualityReports([]);
    setQualitySummary(null);
    setQualityOpen(true);
    loadQuality(taskId);
  };

  const handleAckAlert = async (id: number) => {
    try {
      await ackIngestAlert(id);
      message.success("告警已确认");
      loadMonitor();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "确认告警失败");
    }
  };

  const getDatasourceName = (id: number) =>
    datasources.find((d) => d.id === id)?.name || `#${id}`;

  // --- 表格列定义 ---
  const columns: ColumnsType<IngestTask> = [
    { title: "名称", dataIndex: "name", width: 150, ellipsis: true },
    {
      title: "源类型",
      dataIndex: "source_type",
      width: 80,
      render: (st: IngestSourceType) => <Tag color="blue">{sourceTypeLabel[st]}</Tag>,
    },
    {
      title: "源 URL",
      dataIndex: "source_url",
      width: 220,
      ellipsis: true,
      render: (v: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {v}
        </Text>
      ),
    },
    {
      title: "目标数据源",
      dataIndex: "target_datasource_id",
      width: 120,
      render: (id: number) => getDatasourceName(id),
    },
    { title: "目标表", dataIndex: "target_table", width: 140, ellipsis: true },
    {
      title: "冲突策略",
      dataIndex: "conflict_strategy",
      width: 90,
      render: (cs: IngestConflictStrategy) => (
        <Tag color={cs === "upsert" ? "green" : cs === "skip" ? "orange" : "red"}>
          {conflictLabel[cs]}
        </Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (status: string) => (
        <Tag color={statusColor[status] || "default"}>
          {status === "active" ? "启用" : status === "paused" ? "暂停" : "错误"}
        </Tag>
      ),
    },
    {
      title: "调度",
      width: 110,
      render: (_, r) =>
        r.scheduler_enabled ? (
          <Tag color="purple" title={r.cron_expression}>
            {r.cron_expression || "已启用"}
          </Tag>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: "重试",
      width: 70,
      render: (_, r) => (
        <Text type={r.retry_count > 0 ? "danger" : "secondary"}>
          {r.retry_count}/{r.max_retries}
        </Text>
      ),
    },
    {
      title: "最近爬取",
      dataIndex: "last_sync_at",
      width: 160,
      render: (v: string | null) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      width: 230,
      render: (_, record) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => handleViewLogs(record.id)}
            title="查看日志"
          />
          <Button
            type="link"
            size="small"
            icon={<SafetyCertificateOutlined />}
            onClick={() => handleViewQuality(record.id)}
            title="查看质量报告"
          />
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleRun(record.id)}
            title="执行爬取"
          />
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleOpenEdit(record)}
            title="编辑"
          />
          <Popconfirm
            title="确定删除此爬取任务？"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              title="删除"
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const logColumns: ColumnsType<IngestLog> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (s: IngestLogStatus) => (
        <Tag color={logStatusColor[s]}>{logStatusLabel[s]}</Tag>
      ),
    },
    { title: "读取", dataIndex: "rows_read", width: 70, align: "right" as const },
    { title: "写入", dataIndex: "rows_written", width: 70, align: "right" as const },
    { title: "跳过", dataIndex: "rows_skipped", width: 70, align: "right" as const },
    {
      title: "质量分",
      dataIndex: "quality_score",
      width: 90,
      align: "right" as const,
      render: (v: number) => (
        <Tag
          color={
            v === undefined || v >= 90
              ? "success"
              : v >= 70
                ? "warning"
                : "error"
          }
        >
          {v === undefined ? "-" : Number(v).toFixed(1)}
        </Tag>
      ),
    },
    { title: "耗时(ms)", dataIndex: "duration_ms", width: 100, align: "right" as const },
    {
      title: "开始时间",
      dataIndex: "started_at",
      width: 160,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "错误",
      dataIndex: "error_message",
      ellipsis: true,
      render: (v: string) => v || "-",
    },
  ];

  const qualityReportColumns: ColumnsType<IngestQualityReport> = [
    { title: "字段", dataIndex: "field", width: 140, ellipsis: true },
    {
      title: "规则",
      dataIndex: "rule",
      width: 100,
      render: (r: string) => <Tag color="blue">{r}</Tag>,
    },
    {
      title: "总数",
      dataIndex: "total_count",
      width: 70,
      align: "right" as const,
    },
    {
      title: "通过",
      dataIndex: "passed_count",
      width: 70,
      align: "right" as const,
    },
    {
      title: "失败",
      dataIndex: "failed_count",
      width: 70,
      align: "right" as const,
      render: (v: number) => (
        <Text type={v > 0 ? "danger" : "secondary"}>{v}</Text>
      ),
    },
    {
      title: "通过率",
      dataIndex: "pass_rate",
      width: 100,
      render: (v: number) => (
        <Tag color={v >= 90 ? "success" : v >= 70 ? "warning" : "error"}>
          {v.toFixed(1)}%
        </Tag>
      ),
    },
    {
      title: "失败样本",
      dataIndex: "failure_samples",
      ellipsis: true,
      render: (samples: Array<{ value: unknown; reason: string }>) => {
        if (!samples || samples.length === 0) {
          return <Text type="secondary">-</Text>;
        }
        const preview = samples
          .slice(0, 3)
          .map((s) => JSON.stringify(s.value))
          .join(", ");
        return (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {preview}
            {samples.length > 3 ? ` ... (+${samples.length - 3})` : ""}
          </Text>
        );
      },
    },
    {
      title: "报告时间",
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
  ];

  const alertColumns: ColumnsType<IngestAlert> = [
    {
      title: "级别",
      dataIndex: "level",
      width: 70,
      render: (level: AlertLevel) => (
        <Tag color={level === "error" ? "error" : "warning"}>
          {level === "error" ? "错误" : "警告"}
        </Tag>
      ),
    },
    {
      title: "任务",
      dataIndex: "task_id",
      width: 90,
      render: (id: number) => (
        <Text type="secondary">#{id}</Text>
      ),
    },
    {
      title: "内容",
      dataIndex: "message",
      ellipsis: true,
      render: (v: string) => v || "-",
    },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "状态",
      dataIndex: "acknowledged",
      width: 90,
      render: (acked: boolean) =>
        acked ? <Tag color="default">已确认</Tag> : <Tag color="processing">待处理</Tag>,
    },
    {
      title: "操作",
      width: 90,
      render: (_: unknown, record: IngestAlert) =>
        record.acknowledged ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            —
          </Text>
        ) : (
          <Button
            type="link"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={() => handleAckAlert(record.id)}
          >
            确认
          </Button>
        ),
    },
  ];

  const fieldHealthColumns: ColumnsType<IngestFieldHealth> = [
    { title: "字段", dataIndex: "field", width: 140, ellipsis: true },
    {
      title: "规则",
      dataIndex: "rule",
      width: 100,
      render: (r: string) => <Tag color="blue">{r}</Tag>,
    },
    {
      title: "平均通过率",
      dataIndex: "avg_pass_rate",
      width: 110,
      align: "right" as const,
      render: (v: number) => (
        <Tag color={v >= 90 ? "success" : v >= 70 ? "warning" : "error"}>
          {Number(v).toFixed(1)}%
        </Tag>
      ),
    },
    {
      title: "最近通过率",
      dataIndex: "last_pass_rate",
      width: 110,
      align: "right" as const,
      render: (v: number) => (
        <Text type={v >= 90 ? "success" : v >= 70 ? "warning" : "danger"}>
          {Number(v).toFixed(1)}%
        </Text>
      ),
    },
    {
      title: "检查次数",
      dataIndex: "total_checks",
      width: 90,
      align: "right" as const,
    },
    {
      title: "失败次数",
      dataIndex: "total_failures",
      width: 90,
      align: "right" as const,
      render: (v: number) => (
        <Text type={v > 0 ? "danger" : "secondary"}>{v}</Text>
      ),
    },
    {
      title: "样本数",
      dataIndex: "samples",
      width: 70,
      align: "right" as const,
      render: (v: number) => <Text type="secondary">{v}</Text>,
    },
    {
      title: "最近报告",
      dataIndex: "last_report_at",
      width: 160,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
      <Space
        style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}
      >
        <Title level={4}>数据爬取</Title>
        <Space>
          <Badge count={unackCount} size="small" offset={[-2, 2]}>
            <Button
              icon={<DashboardOutlined />}
              onClick={() => setMonitorOpen(true)}
            >
              监控面板
            </Button>
          </Badge>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleOpenCreate}
          >
            新建爬取任务
          </Button>
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={tasks}
        rowKey="id"
        size="small"
        loading={loading}
        pagination={false}
        locale={{ emptyText: "暂无爬取任务，点击右上角按钮创建第一个任务" }}
      />

      {/* 创建/编辑对话框 */}
      <Modal
        open={dialogOpen}
        title={editingId ? "编辑爬取任务" : "新建爬取任务"}
        onCancel={handleCloseDialog}
        onOk={handleSave}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={980}
      >
        {editingTask && (
          <Form layout="vertical" size="small">
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item label="任务名称" required>
                  <Input
                    value={editingTask.name}
                    onChange={(e) =>
                      setEditingTask({ ...editingTask, name: e.target.value })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={10}>
                <Form.Item label="描述">
                  <Input
                    value={editingTask.description || ""}
                    onChange={(e) =>
                      setEditingTask({ ...editingTask, description: e.target.value })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="源类型" required>
                  <Select
                    value={editingTask.source_type}
                    onChange={handleSourceTypeChange}
                    options={[
                      { value: "api", label: "REST/JSON API" },
                      { value: "html", label: "网页 HTML" },
                      { value: "file", label: "文件下载" },
                      { value: "rss", label: "RSS/Atom" },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={16}>
                <Form.Item label="源 URL" required>
                  <Input
                    value={editingTask.source_url}
                    onChange={(e) =>
                      setEditingTask({ ...editingTask, source_url: e.target.value })
                    }
                    placeholder="https://example.com/api/data"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="鉴权类型">
                  <Select
                    value={editingTask.auth_type}
                    onChange={(v: IngestAuthType) =>
                      setEditingTask({ ...editingTask, auth_type: v })
                    }
                    options={[
                      { value: "none", label: "无鉴权" },
                      { value: "api_key", label: "API Key" },
                      { value: "bearer", label: "Bearer Token" },
                      { value: "basic", label: "Basic Auth" },
                      { value: "custom", label: "自定义" },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>

            {/* 按源类型动态渲染解析配置 */}
            <Text strong>
              <GlobalOutlined /> 解析配置（{sourceTypeLabel[editingTask.source_type]}）
            </Text>
            <div style={{ marginTop: 8, marginBottom: 16, padding: 12, background: "#fafafa", borderRadius: 4 }}>
              {editingTask.source_type === "api" && (
                <Row gutter={16}>
                  <Col span={10}>
                    <Form.Item label="items_path（JSONPath，定位列表数据）">
                      <Input
                        value={(editingTask.parse_config.items_path as string) ?? ""}
                        onChange={(e) => updateParseField("items_path", e.target.value)}
                        placeholder="data.items"
                      />
                    </Form.Item>
                  </Col>
                  <Col span={10}>
                    <Form.Item label="next_page_path（下一页 JSONPath，留空不翻页）">
                      <Input
                        value={(editingTask.parse_config.next_page_path as string) ?? ""}
                        onChange={(e) => updateParseField("next_page_path", e.target.value)}
                        placeholder="data.next"
                      />
                    </Form.Item>
                  </Col>
                  <Col span={4}>
                    <Form.Item label="next_page_max">
                      <InputNumber
                        value={(editingTask.parse_config.next_page_max as number) ?? 0}
                        onChange={(v) => updateParseField("next_page_max", v ?? 0)}
                        min={0}
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  </Col>
                </Row>
              )}

              {editingTask.source_type === "html" && (
                <>
                  <Row gutter={16}>
                    <Col span={6}>
                      <Form.Item label="选择器类型">
                        <Select
                          value={(editingTask.parse_config.selector_type as string) ?? "css"}
                          onChange={(v) => updateParseField("selector_type", v)}
                          options={[
                            { value: "css", label: "CSS" },
                            { value: "xpath", label: "XPath" },
                          ]}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={18}>
                      <Form.Item label="container_selector（行容器选择器）" required>
                        <Input
                          value={(editingTask.parse_config.container_selector as string) ?? ""}
                          onChange={(e) => updateParseField("container_selector", e.target.value)}
                          placeholder="//div[@class='item'] 或 .item-list .item"
                        />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item
                    label="fields（字段提取配置：字段名 → {selector, attr} 或 selector 字符串）"
                    validateStatus={fieldsJsonError ? "error" : ""}
                    help={fieldsJsonError || "JSON 对象，attr 默认 text，可选 text/html/href/src 等"}
                  >
                    <TextArea
                      value={fieldsDraftJson}
                      onChange={(e) => setFieldsDraftJson(e.target.value)}
                      rows={6}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                      placeholder={'{\n  "title": ".title",\n  "link": {"selector": "a", "attr": "href"}\n}'}
                    />
                  </Form.Item>
                  <Row gutter={16}>
                    <Col span={10}>
                      <Form.Item label="next_page_selector（下一页链接选择器）">
                        <Input
                          value={(editingTask.parse_config.next_page_selector as string) ?? ""}
                          onChange={(e) => updateParseField("next_page_selector", e.target.value)}
                          placeholder="a.next-page"
                        />
                      </Form.Item>
                    </Col>
                    <Col span={6}>
                      <Form.Item label="next_page_attr">
                        <Input
                          value={(editingTask.parse_config.next_page_attr as string) ?? "href"}
                          onChange={(e) => updateParseField("next_page_attr", e.target.value)}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={4}>
                      <Form.Item label="next_page_max">
                        <InputNumber
                          value={(editingTask.parse_config.next_page_max as number) ?? 0}
                          onChange={(v) => updateParseField("next_page_max", v ?? 0)}
                          min={0}
                          style={{ width: "100%" }}
                        />
                      </Form.Item>
                    </Col>
                  </Row>
                </>
              )}

              {editingTask.source_type === "file" && (
                <Row gutter={16}>
                  <Col span={6}>
                    <Form.Item label="文件格式" required>
                      <Select
                        value={(editingTask.parse_config.format as string) ?? "csv"}
                        onChange={(v) => updateParseField("format", v)}
                        options={[
                          { value: "csv", label: "CSV" },
                          { value: "xlsx", label: "Excel" },
                          { value: "json", label: "JSON" },
                        ]}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item label="encoding">
                      <Input
                        value={(editingTask.parse_config.encoding as string) ?? "utf-8"}
                        onChange={(e) => updateParseField("encoding", e.target.value)}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item label="delimiter（CSV 分隔符）">
                      <Input
                        value={(editingTask.parse_config.delimiter as string) ?? ","}
                        onChange={(e) => updateParseField("delimiter", e.target.value)}
                        disabled={(editingTask.parse_config.format as string) !== "csv"}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item label="sheet（Excel 工作表名）">
                      <Input
                        value={(editingTask.parse_config.sheet as string) ?? ""}
                        onChange={(e) => updateParseField("sheet", e.target.value)}
                        disabled={(editingTask.parse_config.format as string) !== "xlsx"}
                        placeholder="Sheet1"
                      />
                    </Form.Item>
                  </Col>
                  <Col span={24}>
                    <Form.Item label="items_path（JSON 文件内列表路径，留空则整体作为数组）">
                      <Input
                        value={(editingTask.parse_config.items_path as string) ?? ""}
                        onChange={(e) => updateParseField("items_path", e.target.value)}
                        disabled={(editingTask.parse_config.format as string) !== "json"}
                        placeholder="data.items"
                      />
                    </Form.Item>
                  </Col>
                </Row>
              )}

              {editingTask.source_type === "rss" && (
                <Form.Item label="包含 Feed 元数据">
                  <Switch
                    checked={Boolean(editingTask.parse_config.include_feed_metadata)}
                    onChange={(v) => updateParseField("include_feed_metadata", v)}
                  />
                  <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
                    开启后将 feed 级元数据（title/link/description 等）以 feed_ 前缀合并到每条 entry
                  </Text>
                </Form.Item>
              )}
            </div>

            {/* 请求配置（通用） */}
            <Text strong>请求配置</Text>
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={5}>
                <Form.Item label="并发数">
                  <InputNumber
                    value={(editingTask.request_config.concurrent_requests as number) ?? 8}
                    onChange={(v) => updateRequestField("concurrent_requests", v ?? 8)}
                    min={1}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
              <Col span={5}>
                <Form.Item label="超时(秒)">
                  <InputNumber
                    value={(editingTask.request_config.timeout as number) ?? 30}
                    onChange={(v) => updateRequestField("timeout", v ?? 30)}
                    min={1}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
              <Col span={5}>
                <Form.Item label="下载延迟(秒)">
                  <InputNumber
                    value={(editingTask.request_config.download_delay as number) ?? 0}
                    onChange={(v) => updateRequestField("download_delay", v ?? 0)}
                    min={0}
                    step={0.1}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
              <Col span={9}>
                <Form.Item label="User-Agent">
                  <Input
                    value={(editingTask.request_config.user_agent as string) ?? "rdbase-ingest/1.0"}
                    onChange={(e) => updateRequestField("user_agent", e.target.value)}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={6}>
                <Form.Item label="启用 Cookies">
                  <Switch
                    checked={Boolean(editingTask.request_config.cookies_enabled)}
                    onChange={(v) => updateRequestField("cookies_enabled", v)}
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="遵守 robots.txt">
                  <Switch
                    checked={editingTask.obey_robots}
                    onChange={(v) =>
                      setEditingTask({ ...editingTask, obey_robots: v })
                    }
                  />
                </Form.Item>
              </Col>
            </Row>

            {/* 请求头（敏感，加密存储） */}
            <Space
              style={{ width: "100%", justifyContent: "space-between", marginTop: 8 }}
            >
              <Text strong>请求头（敏感，加密存储）</Text>
              <Button size="small" icon={<PlusOutlined />} onClick={addHeader}>
                添加请求头
              </Button>
            </Space>
            {editingId && editingHasHeaders && headersDraft.length === 0 && (
              <Alert
                type="info"
                showIcon
                message="此任务已配置请求头（出于安全考虑不回显）。如需保留原值请勿添加新项；添加新项将整体覆盖原请求头。"
                style={{ margin: "8px 0" }}
              />
            )}
            {headersDraft.length > 0 && (
              <Table
                size="small"
                rowKey={(_, idx) => String(idx)}
                dataSource={headersDraft.map((h, idx) => ({ ...h, key: idx }))}
                pagination={false}
                style={{ marginTop: 8 }}
                columns={[
                  {
                    title: "Header 名",
                    render: (_, _r, idx) => (
                      <Input
                        value={headersDraft[idx].key}
                        onChange={(e) => updateHeader(idx, "key", e.target.value)}
                        placeholder="Authorization"
                        size="small"
                      />
                    ),
                  },
                  {
                    title: "值",
                    render: (_, _r, idx) => (
                      <Input.Password
                        value={headersDraft[idx].value}
                        onChange={(e) => updateHeader(idx, "value", e.target.value)}
                        placeholder="Bearer xxx / api_key xxx"
                        size="small"
                      />
                    ),
                  },
                  {
                    title: "",
                    width: 40,
                    render: (_, _r, idx) => (
                      <Button
                        type="link"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => removeHeader(idx)}
                      />
                    ),
                  },
                ]}
              />
            )}

            {/* 目标写入 */}
            <Text strong style={{ display: "block", marginTop: 16 }}>
              目标写入
            </Text>
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={10}>
                <Form.Item label="目标数据源" required>
                  <Select
                    value={editingTask.target_datasource_id || undefined}
                    onChange={(v) =>
                      setEditingTask({ ...editingTask, target_datasource_id: v })
                    }
                    options={datasources.map((ds) => ({
                      value: ds.id,
                      label: `${ds.name} (${ds.engine})`,
                    }))}
                    placeholder="选择数据源"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="目标表名" required>
                  <Input
                    value={editingTask.target_table}
                    onChange={(e) =>
                      setEditingTask({ ...editingTask, target_table: e.target.value })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="冲突策略">
                  <Select
                    value={editingTask.conflict_strategy}
                    onChange={(v: IngestConflictStrategy) =>
                      setEditingTask({ ...editingTask, conflict_strategy: v })
                    }
                    options={[
                      { value: "upsert", label: "冲突则更新" },
                      { value: "skip", label: "冲突则跳过" },
                      { value: "error", label: "冲突则报错" },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item label="批大小">
                  <InputNumber
                    value={editingTask.batch_size}
                    onChange={(v) =>
                      setEditingTask({ ...editingTask, batch_size: v ?? 500 })
                    }
                    min={1}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="启用定时调度">
                  <Switch
                    checked={editingTask.scheduler_enabled}
                    onChange={(v) =>
                      setEditingTask({ ...editingTask, scheduler_enabled: v })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="Cron 表达式">
                  <Input
                    value={editingTask.cron_expression}
                    onChange={(e) =>
                      setEditingTask({ ...editingTask, cron_expression: e.target.value })
                    }
                    placeholder="*/5 * * * *"
                    disabled={!editingTask.scheduler_enabled}
                  />
                </Form.Item>
              </Col>
            </Row>

            {/* 字段映射 */}
            <Space
              style={{
                width: "100%",
                justifyContent: "space-between",
                marginTop: 16,
                marginBottom: 8,
              }}
            >
              <Text strong>字段映射</Text>
              <Button size="small" icon={<PlusOutlined />} onClick={addMapping}>
                添加映射
              </Button>
            </Space>
            <Table
              size="small"
              rowKey={(_, idx) => String(idx)}
              dataSource={editingTask.field_mappings.map((m, idx) => ({
                ...m,
                key: idx,
              }))}
              pagination={false}
              columns={[
                {
                  title: "源字段",
                  render: (_, record, idx) => (
                    <Input
                      value={record.source_field}
                      onChange={(e) =>
                        updateMapping(idx, { source_field: e.target.value })
                      }
                      disabled={record.mapping_type === "constant"}
                      placeholder="源字段名"
                      size="small"
                    />
                  ),
                },
                {
                  title: "映射类型",
                  width: 120,
                  render: (_, record, idx) => (
                    <Select
                      size="small"
                      value={record.mapping_type}
                      onChange={(v) => updateMapping(idx, { mapping_type: v })}
                      style={{ width: "100%" }}
                      options={[
                        { value: "direct", label: "直接映射" },
                        { value: "constant", label: "常量" },
                      ]}
                    />
                  ),
                },
                {
                  title: "常量值",
                  width: 120,
                  render: (_, record, idx) => (
                    <Input
                      value={record.fixed_value}
                      onChange={(e) =>
                        updateMapping(idx, { fixed_value: e.target.value })
                      }
                      disabled={record.mapping_type !== "constant"}
                      placeholder="常量值"
                      size="small"
                    />
                  ),
                },
                {
                  title: "目标字段",
                  render: (_, record, idx) => (
                    <Input
                      value={record.target_field}
                      onChange={(e) =>
                        updateMapping(idx, { target_field: e.target.value })
                      }
                      placeholder="目标字段名"
                      size="small"
                    />
                  ),
                },
                {
                  title: "主键",
                  width: 60,
                  render: (_, record, idx) => (
                    <Checkbox
                      checked={record.is_pk}
                      onChange={(e) => updateMapping(idx, { is_pk: e.target.checked })}
                    />
                  ),
                },
                {
                  title: "",
                  width: 40,
                  render: (_, _r, idx) => (
                    <Button
                      type="link"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => removeMapping(idx)}
                    />
                  ),
                },
              ]}
            />

            {/* 清洗配置 */}
            <Space
              style={{
                width: "100%",
                justifyContent: "space-between",
                marginTop: 16,
                marginBottom: 8,
              }}
            >
              <Text strong>清洗配置（P8-Q1）</Text>
              <Space size="small">
                <Button
                  size="small"
                  onClick={() => setCleanConfigDraftJson(JSON.stringify(defaultCleanConfig(), null, 2))}
                >
                  填充模板
                </Button>
                <Button
                  size="small"
                  onClick={() => setCleanConfigDraftJson("{}")}
                >
                  清空
                </Button>
              </Space>
            </Space>
            <Form.Item
              validateStatus={cleanConfigJsonError ? "error" : ""}
              help={
                cleanConfigJsonError ||
                "JSON 对象。rules 数组按序执行 on_missing/cast_type/normalize/strip_html/enum_map；dedup 控制去重"
              }
            >
              <TextArea
                value={cleanConfigDraftJson}
                onChange={(e) => setCleanConfigDraftJson(e.target.value)}
                rows={8}
                style={{ fontFamily: "monospace", fontSize: 12 }}
                placeholder={
                  '{\n  "rules": [\n    {"field": "name", "op": "on_missing", "strategy": "fill_default", "default": ""},\n    {"field": "age", "op": "cast_type", "cast_type": "int"},\n    {"field": "phone", "op": "normalize", "normalizer": "phone"}\n  ],\n  "dedup": {"enabled": true, "fields": ["id"], "ttl_hours": 24}\n}'
                }
              />
            </Form.Item>

            {/* 校验配置（P8-Q2） */}
            <Space
              style={{
                width: "100%",
                justifyContent: "space-between",
                marginTop: 8,
                marginBottom: 8,
              }}
            >
              <Text strong>校验配置（P8-Q2）</Text>
              <Space size="small">
                <Button
                  size="small"
                  onClick={() =>
                    setValidationConfigDraftJson(
                      JSON.stringify(defaultValidationConfig(), null, 2),
                    )
                  }
                >
                  填充模板
                </Button>
                <Button
                  size="small"
                  onClick={() => setValidationConfigDraftJson("{}")}
                >
                  清空
                </Button>
              </Space>
            </Space>
            <Form.Item
              validateStatus={validationConfigJsonError ? "error" : ""}
              help={
                validationConfigJsonError ||
                "JSON 对象。rules 数组按序执行 required/range/regex/enum/unique/expression；校验失败不丢弃，记录到质量报告"
              }
            >
              <TextArea
                value={validationConfigDraftJson}
                onChange={(e) => setValidationConfigDraftJson(e.target.value)}
                rows={8}
                style={{ fontFamily: "monospace", fontSize: 12 }}
                placeholder={
                  '{\n  "rules": [\n    {"field": "name", "op": "required"},\n    {"field": "age", "op": "range", "min": 0, "max": 150},\n    {"field": "email", "op": "regex", "pattern": "^[^@]+@[^@]+$"},\n    {"field": "status", "op": "enum", "values": ["active", "inactive"]},\n    {"field": "id", "op": "unique"},\n    {"field": "age", "op": "expression", "expr": "value > 0"}\n  ]\n}'
                }
              />
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* 日志对话框 */}
      <Modal
        open={logsOpen}
        title={
          <span>
            爬取日志
            {viewingLogsTaskId && (
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                （任务 #{viewingLogsTaskId}）
              </Text>
            )}
          </span>
        }
        onCancel={() => setLogsOpen(false)}
        footer={null}
        width={900}
      >
        <Space style={{ marginBottom: 8, width: "100%", justifyContent: "flex-end" }}>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => loadLogs(viewingLogsTaskId ?? undefined)}
          >
            刷新
          </Button>
        </Space>
        <Table
          columns={logColumns}
          dataSource={logs}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: "暂无日志记录" }}
        />
      </Modal>

      {/* 质量报告对话框（P8-Q2） */}
      <Modal
        open={qualityOpen}
        title={
          <span>
            数据质量报告
            {viewingQualityTaskId && (
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                （任务 #{viewingQualityTaskId}）
              </Text>
            )}
          </span>
        }
        onCancel={() => setQualityOpen(false)}
        footer={null}
        width={960}
      >
        <Space style={{ marginBottom: 8, width: "100%", justifyContent: "flex-end" }}>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => loadQuality(viewingQualityTaskId ?? undefined)}
          >
            刷新
          </Button>
        </Space>
        {qualityLoading ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin tip="加载质量报告中..." />
          </div>
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}>
                <Statistic
                  title="规则数"
                  value={qualitySummary?.total_rules ?? 0}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="平均通过率"
                  value={qualitySummary?.avg_pass_rate ?? 0}
                  suffix="%"
                  valueStyle={{
                    color:
                      (qualitySummary?.avg_pass_rate ?? 100) >= 90
                        ? "#3f8600"
                        : (qualitySummary?.avg_pass_rate ?? 100) >= 70
                          ? "#faad14"
                          : "#cf1322",
                  }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="失败样本"
                  value={qualitySummary?.total_failures ?? 0}
                  valueStyle={{
                    color: (qualitySummary?.total_failures ?? 0) > 0 ? "#cf1322" : undefined,
                  }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="最差字段"
                  value={qualitySummary?.worst_field || "-"}
                  suffix={qualitySummary?.worst_rule ? ` (${qualitySummary.worst_rule})` : ""}
                />
              </Col>
            </Row>
            <Table
              columns={qualityReportColumns}
              dataSource={qualityReports}
              rowKey="id"
              size="small"
              pagination={{ pageSize: 20 }}
              locale={{ emptyText: "暂无质量报告（任务尚未执行或未配置校验规则）" }}
            />
          </>
        )}
      </Modal>

      {/* 执行结果对话框 */}
      <Modal
        open={runResultOpen}
        title="爬取执行结果"
        onCancel={() => setRunResultOpen(false)}
        footer={
          <Button type="primary" onClick={() => setRunResultOpen(false)}>
            关闭
          </Button>
        }
        width={760}
      >
        {running && !runResult ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin tip="爬取执行中（子进程运行 Scrapy，可能耗时较长）..." />
          </div>
        ) : runResult ? (
          <div>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}>
                <Statistic
                  title="退出码"
                  value={runResult.returncode}
                  valueStyle={{
                    color: runResult.returncode === 0 ? "#3f8600" : "#cf1322",
                  }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="执行状态"
                  value={
                    runResult.log
                      ? logStatusLabel[runResult.log.status]
                      : "无日志"
                  }
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="读取行数"
                  value={runResult.log?.rows_read ?? 0}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="写入行数"
                  value={runResult.log?.rows_written ?? 0}
                  valueStyle={{ color: "#3f8600" }}
                />
              </Col>
            </Row>
            {runResult.log?.error_message && (
              <Alert
                type="error"
                message="错误信息"
                description={runResult.log.error_message}
                style={{ marginBottom: 16 }}
              />
            )}
            {runResult.stderr && (
              <>
                <Text strong>子进程 stderr 输出：</Text>
                <Paragraph>
                  <pre
                    style={{
                      background: "#f5f5f5",
                      padding: 12,
                      borderRadius: 4,
                      maxHeight: 240,
                      overflow: "auto",
                      fontSize: 12,
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {runResult.stderr}
                  </pre>
                </Paragraph>
              </>
            )}
          </div>
        ) : null}
      </Modal>

      {/* 监控面板对话框 */}
      <Modal
        open={monitorOpen}
        title="爬取监控面板"
        onCancel={() => setMonitorOpen(false)}
        footer={null}
        width={960}
      >
        <Space style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }}>
          <Space>
            <Text type="secondary">统计范围</Text>
            <Select
              size="small"
              value={statsDays ?? "all"}
              onChange={(v) => setStatsDays(v === "all" ? null : Number(v))}
              style={{ width: 120 }}
              options={[
                { value: 7, label: "近 7 天" },
                { value: 30, label: "近 30 天" },
                { value: "all", label: "全部" },
              ]}
            />
          </Space>
          <Space>
            <Checkbox
              checked={onlyUnacked}
              onChange={(e) => setOnlyUnacked(e.target.checked)}
            >
              仅看未确认
            </Checkbox>
            <Button size="small" icon={<ReloadOutlined />} onClick={loadMonitor}>
              刷新
            </Button>
          </Space>
        </Space>

        <Spin spinning={monitorLoading}>
          <Row gutter={16} style={{ marginBottom: 8 }}>
            <Col span={6}>
              <Statistic
                title="成功率"
                value={stats?.success_rate ?? 0}
                suffix="%"
                precision={1}
                valueStyle={{
                  color: (stats?.success_rate ?? 100) >= 90 ? "#3f8600" : "#cf1322",
                }}
              />
            </Col>
            <Col span={6}>
              <Statistic title="执行次数" value={stats?.total ?? 0} />
            </Col>
            <Col span={6}>
              <Statistic
                title="失败次数"
                value={stats?.failed ?? 0}
                valueStyle={{ color: (stats?.failed ?? 0) > 0 ? "#cf1322" : undefined }}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="平均耗时"
                value={stats?.avg_duration_ms ?? 0}
                suffix="ms"
              />
            </Col>
          </Row>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}>
              <Statistic title="成功" value={stats?.succeeded ?? 0} />
            </Col>
            <Col span={6}>
              <Statistic title="部分成功" value={stats?.partial ?? 0} />
            </Col>
            <Col span={6}>
              <Statistic title="累计写入行" value={stats?.total_rows_written ?? 0} />
            </Col>
            <Col span={6}>
              <Statistic
                title="平均质量分"
                value={stats?.avg_quality_score ?? 0}
                precision={1}
                valueStyle={{
                  color:
                    (stats?.avg_quality_score ?? 100) >= 80
                      ? "#3f8600"
                      : (stats?.avg_quality_score ?? 100) >= 60
                        ? "#faad14"
                        : "#cf1322",
                }}
              />
            </Col>
          </Row>

          <Title level={5} style={{ margin: 0, marginBottom: 8 }}>
            字段健康度（P8-Q3，按平均通过率升序，最差字段在前）
          </Title>
          <Table
            columns={fieldHealthColumns}
            dataSource={fieldHealth}
            rowKey={(r) => `${r.field}:${r.rule}`}
            size="small"
            pagination={{ pageSize: 10 }}
            style={{ marginBottom: 16 }}
            locale={{ emptyText: "暂无字段健康度数据（任务未配置校验规则或尚未执行）" }}
          />

          <Title level={5} style={{ margin: 0, marginBottom: 8 }}>
            失败告警
          </Title>
          <Table
            columns={alertColumns}
            dataSource={alerts}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: onlyUnacked ? "暂无未确认告警" : "暂无告警记录" }}
          />
        </Spin>
      </Modal>
    </div>
  );
}
