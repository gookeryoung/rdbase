import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
  Spin,
  Alert,
  Input,
  Form,
  Statistic,
  Row,
  Col,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  PlayCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
  ScheduleOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";

import {
  listSyncConfigs,
  createSyncConfig,
  updateSyncConfig,
  deleteSyncConfig,
  triggerSync,
  previewSync,
  batchTriggerSync,
  updateSchedule,
  listSyncLogs,
} from "@/api/sync";
import { listDatasources } from "@/api/datasources";
import type {
  DataSource,
  SyncConfig,
  SyncConfigCreate,
  SyncFieldMapping,
  SyncLog,
  SyncPreview,
  SyncBatchResult,
  SyncScheduleUpdate,
} from "@/types";

const { Text, Title } = Typography;

const emptyMapping = (): SyncFieldMapping => ({
  source_field: "",
  target_field: "",
  mapping_type: "direct",
  fixed_value: "",
  is_pk: false,
});

const createEmptyConfig = (): SyncConfigCreate => ({
  name: "",
  description: "",
  source_table: "",
  source_schema: "",
  source_db_alias: "default",
  target_datasource_id: 0,
  target_table: "",
  target_schema: "",
  sync_mode: "incremental",
  status: "active",
  timestamp_field: "updated_at",
  batch_size: 500,
  scheduler_enabled: false,
  cron_expression: "",
  max_retries: 3,
  field_mappings: [emptyMapping()],
});

// 状态标签颜色
const statusColor: Record<string, string> = {
  active: "success",
  paused: "default",
  error: "error",
};

// 模式标签
const modeLabel: Record<string, string> = {
  full: "全量",
  incremental: "增量",
};

export default function SyncPage() {
  const [configs, setConfigs] = useState<SyncConfig[]>([]);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [viewingLogsConfigId, setViewingLogsConfigId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  // 创建/编辑对话框
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingConfig, setEditingConfig] = useState<SyncConfigCreate | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  // 调度设置对话框
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleConfig, setScheduleConfig] = useState<SyncConfig | null>(null);
  const [scheduleForm, setScheduleForm] = useState<SyncScheduleUpdate>({
    scheduler_enabled: false,
    cron_expression: "",
    max_retries: 3,
  });

  // 预览对话框
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<SyncPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // 批量触发
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchForceFull, setBatchForceFull] = useState(false);
  const [batchStopOnError, setBatchStopOnError] = useState(false);
  const [batchResult, setBatchResult] = useState<SyncBatchResult | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);

  // 加载数据
  const loadConfigs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSyncConfigs();
      setConfigs(data.items);
    } catch {
      message.error("加载同步配置失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDatasources = useCallback(async () => {
    try {
      const data = await listDatasources();
      setDatasources(data);
    } catch {
      // 数据源接口可能不可用
    }
  }, []);

  const loadLogs = useCallback(async (configId?: number) => {
    try {
      const data = await listSyncLogs(configId, 50);
      setLogs(data.items);
    } catch {
      message.error("加载同步日志失败");
    }
  }, []);

  useEffect(() => {
    loadConfigs();
    loadDatasources();
  }, [loadConfigs, loadDatasources]);

  // --- 创建/编辑 ---
  const handleOpenCreate = () => {
    setEditingConfig(createEmptyConfig());
    setEditingId(null);
    setDialogOpen(true);
  };

  const handleOpenEdit = (config: SyncConfig) => {
    setEditingConfig({
      name: config.name,
      description: config.description,
      source_table: config.source_table,
      source_schema: config.source_schema,
      source_db_alias: config.source_db_alias,
      target_datasource_id: config.target_datasource_id,
      target_table: config.target_table,
      target_schema: config.target_schema,
      sync_mode: config.sync_mode,
      status: config.status,
      timestamp_field: config.timestamp_field,
      batch_size: config.batch_size,
      scheduler_enabled: config.scheduler_enabled,
      cron_expression: config.cron_expression,
      max_retries: config.max_retries,
      field_mappings: config.field_mappings.map((m) => ({
        source_field: m.source_field,
        target_field: m.target_field,
        mapping_type: m.mapping_type,
        fixed_value: m.fixed_value,
        is_pk: m.is_pk,
      })),
    });
    setEditingId(config.id);
    setDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setDialogOpen(false);
    setEditingConfig(null);
    setEditingId(null);
  };

  const handleSave = async () => {
    if (!editingConfig) return;
    if (!editingConfig.name.trim()) {
      message.warning("请填写配置名称");
      return;
    }
    if (!editingConfig.source_table.trim()) {
      message.warning("请填写源表名");
      return;
    }
    if (!editingConfig.target_datasource_id) {
      message.warning("请选择目标数据源");
      return;
    }
    if (!editingConfig.target_table.trim()) {
      message.warning("请填写目标表名");
      return;
    }
    const validMappings = editingConfig.field_mappings.filter(
      (m) => m.source_field.trim() || m.mapping_type === "constant"
    );
    if (validMappings.length === 0) {
      message.warning("请添加至少一个有效字段映射");
      return;
    }

    setSaving(true);
    try {
      if (editingId) {
        await updateSyncConfig(editingId, editingConfig);
        message.success("同步配置已更新");
      } else {
        await createSyncConfig(editingConfig);
        message.success("同步配置已创建");
      }
      handleCloseDialog();
      loadConfigs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteSyncConfig(id);
      message.success("同步配置已删除");
      loadConfigs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleTriggerSync = async (id: number) => {
    try {
      const result = await triggerSync(id, { confirm: true, force_full: false });
      if (result.status === "success") {
        message.success(
          `同步成功：读取 ${result.rows_read}，写入 ${result.rows_written}，跳过 ${result.rows_skipped}`
        );
      } else {
        message.error(
          `同步失败：读取 ${result.rows_read}，写入 ${result.rows_written}，跳过 ${result.rows_skipped}`
        );
      }
      loadConfigs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "同步触发失败");
    }
  };

  const handleViewLogs = (configId: number) => {
    setViewingLogsConfigId(configId);
    loadLogs(configId);
    setLogsOpen(true);
  };

  // --- 字段映射编辑 ---
  const addMapping = () => {
    if (!editingConfig) return;
    setEditingConfig({
      ...editingConfig,
      field_mappings: [...editingConfig.field_mappings, emptyMapping()],
    });
  };

  const removeMapping = (index: number) => {
    if (!editingConfig) return;
    const mappings = [...editingConfig.field_mappings];
    mappings.splice(index, 1);
    setEditingConfig({ ...editingConfig, field_mappings: mappings });
  };

  const updateMapping = (index: number, updates: Partial<SyncFieldMapping>) => {
    if (!editingConfig) return;
    const mappings = [...editingConfig.field_mappings];
    mappings[index] = { ...mappings[index], ...updates };
    setEditingConfig({ ...editingConfig, field_mappings: mappings });
  };

  // --- 调度设置 ---
  const handleOpenSchedule = (config: SyncConfig) => {
    setScheduleConfig(config);
    setScheduleForm({
      scheduler_enabled: config.scheduler_enabled,
      cron_expression: config.cron_expression,
      max_retries: config.max_retries,
    });
    setScheduleOpen(true);
  };

  const handleSaveSchedule = async () => {
    if (!scheduleConfig) return;
    if (scheduleForm.scheduler_enabled && !scheduleForm.cron_expression?.trim()) {
      message.warning("启用调度时须填写 Cron 表达式");
      return;
    }
    try {
      await updateSchedule(scheduleConfig.id, scheduleForm);
      message.success("调度设置已更新");
      setScheduleOpen(false);
      loadConfigs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "调度设置失败");
    }
  };

  // --- 预览 ---
  const handlePreview = async (id: number) => {
    setPreviewOpen(true);
    setPreviewData(null);
    setPreviewLoading(true);
    try {
      const data = await previewSync(id, { force_full: false });
      setPreviewData(data);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "预览失败");
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  };

  // --- 批量触发 ---
  const handleBatchTrigger = async () => {
    setBatchLoading(true);
    setBatchResult(null);
    try {
      const result = await batchTriggerSync({
        config_ids: selectedIds,
        force_full: batchForceFull,
        stop_on_error: batchStopOnError,
        confirm: true,
      });
      setBatchResult(result);
      message.success(
        `批量同步完成：成功 ${result.succeeded}，失败 ${result.failed}，跳过 ${result.skipped}`
      );
      loadConfigs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "批量触发失败");
    } finally {
      setBatchLoading(false);
    }
  };

  const getDatasourceName = (id: number) =>
    datasources.find((d) => d.id === id)?.name || `#${id}`;

  // --- 表格列定义 ---
  const columns: ColumnsType<SyncConfig> = [
    { title: "名称", dataIndex: "name", width: 150, ellipsis: true },
    {
      title: "源表",
      dataIndex: "source_table",
      width: 150,
      render: (_, r) =>
        r.source_schema ? `${r.source_schema}.${r.source_table}` : r.source_table,
    },
    {
      title: "目标数据源",
      dataIndex: "target_datasource_id",
      width: 120,
      render: (id: number) => getDatasourceName(id),
    },
    {
      title: "目标表",
      width: 150,
      render: (_, r) =>
        r.target_schema ? `${r.target_schema}.${r.target_table}` : r.target_table,
    },
    {
      title: "模式",
      dataIndex: "sync_mode",
      width: 80,
      render: (mode: string) => (
        <Tag color={mode === "full" ? "blue" : "cyan"}>
          {modeLabel[mode] || mode}
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
      width: 100,
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
      title: "最近同步",
      dataIndex: "last_sync_at",
      width: 160,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString("zh-CN") : "-",
    },
    {
      title: "操作",
      width: 200,
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
            icon={<FileSearchOutlined />}
            onClick={() => handlePreview(record.id)}
            title="预览"
          />
          <Button
            type="link"
            size="small"
            icon={<ScheduleOutlined />}
            onClick={() => handleOpenSchedule(record)}
            title="调度设置"
          />
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleTriggerSync(record.id)}
            title="执行同步"
          />
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleOpenEdit(record)}
            title="编辑"
          />
          <Popconfirm
            title="确定删除此同步配置？"
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

  // --- 日志表格列 ---
  const logColumns: ColumnsType<SyncLog> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (status: string) => (
        <Tag
          color={
            status === "success" ? "success" : status === "failed" ? "error" : "warning"
          }
        >
          {status === "success" ? "成功" : status === "failed" ? "失败" : "执行中"}
        </Tag>
      ),
    },
    {
      title: "模式",
      dataIndex: "mode",
      width: 80,
      render: (mode: string) => (
        <Tag>{modeLabel[mode] || mode}</Tag>
      ),
    },
    { title: "读取", dataIndex: "rows_read", width: 60, align: "right" as const },
    { title: "写入", dataIndex: "rows_written", width: 60, align: "right" as const },
    { title: "跳过", dataIndex: "rows_skipped", width: 60, align: "right" as const },
    { title: "耗时(ms)", dataIndex: "duration_ms", width: 90, align: "right" as const },
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

  // 预览采样数据列
  const previewSampleColumns =
    previewData && previewData.sample_rows.length > 0
      ? Object.keys(previewData.sample_rows[0]).map((key) => ({
        title: key,
        dataIndex: key,
        ellipsis: true,
        render: (v: unknown) => (v === null || v === undefined ? "-" : String(v)),
      }))
      : [];

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
      <Space
        style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}
      >
        <Title level={4}>数据同步</Title>
        <Space>
          {selectedIds.length > 0 && (
            <Text type="secondary">已选择 {selectedIds.length} 项</Text>
          )}
          <Button
            icon={<ThunderboltOutlined />}
            disabled={selectedIds.length === 0}
            onClick={() => {
              setBatchResult(null);
              setBatchOpen(true);
            }}
          >
            批量触发
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleOpenCreate}
          >
            新建同步配置
          </Button>
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={configs}
        rowKey="id"
        size="small"
        loading={loading}
        pagination={false}
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys) => setSelectedIds(keys as number[]),
        }}
        locale={{ emptyText: "暂无同步配置，点击右上角按钮创建第一个配置" }}
      />

      {/* 创建/编辑对话框 */}
      <Modal
        open={dialogOpen}
        title={editingId ? "编辑同步配置" : "新建同步配置"}
        onCancel={handleCloseDialog}
        onOk={handleSave}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={900}
      >
        {editingConfig && (
          <Form layout="vertical" size="small">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="配置名称" required>
                  <Input
                    value={editingConfig.name}
                    onChange={(e) =>
                      setEditingConfig({ ...editingConfig, name: e.target.value })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="描述">
                  <Input
                    value={editingConfig.description || ""}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        description: e.target.value,
                      })
                    }
                  />
                </Form.Item>
              </Col>
            </Row>

            <Text strong>源表（rdbase 平台库）</Text>
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={8}>
                <Form.Item label="源 Schema">
                  <Input
                    value={editingConfig.source_schema || ""}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        source_schema: e.target.value,
                      })
                    }
                    placeholder="留空使用默认"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="源表名" required>
                  <Input
                    value={editingConfig.source_table}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        source_table: e.target.value,
                      })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="源库 Alias">
                  <Select
                    value={editingConfig.source_db_alias}
                    onChange={(v) =>
                      setEditingConfig({ ...editingConfig, source_db_alias: v })
                    }
                    options={[
                      { value: "default", label: "default" },
                      { value: "readonly", label: "readonly" },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>

            <Text strong>目标表（外部数据源）</Text>
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={8}>
                <Form.Item label="目标数据源" required>
                  <Select
                    value={editingConfig.target_datasource_id || undefined}
                    onChange={(v) =>
                      setEditingConfig({
                        ...editingConfig,
                        target_datasource_id: v,
                      })
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
                <Form.Item label="目标 Schema">
                  <Input
                    value={editingConfig.target_schema || ""}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        target_schema: e.target.value,
                      })
                    }
                    placeholder="留空使用默认"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="目标表名" required>
                  <Input
                    value={editingConfig.target_table}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        target_table: e.target.value,
                      })
                    }
                  />
                </Form.Item>
              </Col>
            </Row>

            <Text strong>同步参数</Text>
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={6}>
                <Form.Item label="同步模式">
                  <Select
                    value={editingConfig.sync_mode}
                    onChange={(v) =>
                      setEditingConfig({ ...editingConfig, sync_mode: v })
                    }
                    options={[
                      { value: "incremental", label: "增量" },
                      { value: "full", label: "全量" },
                    ]}
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="状态">
                  <Select
                    value={editingConfig.status}
                    onChange={(v) =>
                      setEditingConfig({ ...editingConfig, status: v })
                    }
                    options={[
                      { value: "active", label: "启用" },
                      { value: "paused", label: "暂停" },
                    ]}
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="时间戳字段">
                  <Input
                    value={editingConfig.timestamp_field}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        timestamp_field: e.target.value,
                      })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="批大小">
                  <InputNumber
                    value={editingConfig.batch_size}
                    onChange={(v) =>
                      setEditingConfig({
                        ...editingConfig,
                        batch_size: v ?? 500,
                      })
                    }
                    min={1}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col span={8}>
                <Form.Item label="启用定时调度">
                  <Switch
                    checked={editingConfig.scheduler_enabled}
                    onChange={(v) =>
                      setEditingConfig({
                        ...editingConfig,
                        scheduler_enabled: v,
                      })
                    }
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="Cron 表达式">
                  <Input
                    value={editingConfig.cron_expression}
                    onChange={(e) =>
                      setEditingConfig({
                        ...editingConfig,
                        cron_expression: e.target.value,
                      })
                    }
                    placeholder="*/5 * * * *"
                    disabled={!editingConfig.scheduler_enabled}
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="最大重试次数">
                  <InputNumber
                    value={editingConfig.max_retries}
                    onChange={(v) =>
                      setEditingConfig({
                        ...editingConfig,
                        max_retries: v ?? 3,
                      })
                    }
                    min={0}
                    max={10}
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              </Col>
            </Row>

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
              dataSource={editingConfig.field_mappings.map((m, idx) => ({
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
                      onChange={(v) =>
                        updateMapping(idx, { mapping_type: v })
                      }
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
                      onChange={(e) =>
                        updateMapping(idx, { is_pk: e.target.checked })
                      }
                    />
                  ),
                },
                {
                  title: "",
                  width: 40,
                  render: (_, __, idx) => (
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
          </Form>
        )}
      </Modal>

      {/* 日志对话框 */}
      <Modal
        open={logsOpen}
        title={
          <span>
            同步日志
            {viewingLogsConfigId && (
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                （仅显示此配置）
              </Text>
            )}
          </span>
        }
        onCancel={() => setLogsOpen(false)}
        footer={null}
        width={800}
      >
        <Space style={{ marginBottom: 8, width: "100%", justifyContent: "flex-end" }}>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => loadLogs(viewingLogsConfigId ?? undefined)}
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

      {/* 调度设置对话框 */}
      <Modal
        open={scheduleOpen}
        title="调度设置"
        onCancel={() => setScheduleOpen(false)}
        onOk={handleSaveSchedule}
        okText="保存"
        cancelText="取消"
      >
        {scheduleConfig && (
          <Form layout="vertical">
            <Form.Item label="配置名称">
              <Text strong>{scheduleConfig.name}</Text>
            </Form.Item>
            <Form.Item label="启用定时调度">
              <Switch
                checked={scheduleForm.scheduler_enabled}
                onChange={(v) =>
                  setScheduleForm({ ...scheduleForm, scheduler_enabled: v })
                }
              />
            </Form.Item>
            <Form.Item label="Cron 表达式">
              <Input
                value={scheduleForm.cron_expression}
                onChange={(e) =>
                  setScheduleForm({
                    ...scheduleForm,
                    cron_expression: e.target.value,
                  })
                }
                placeholder="*/5 * * * *（每5分钟）"
                disabled={!scheduleForm.scheduler_enabled}
              />
            </Form.Item>
            <Form.Item label="最大重试次数">
              <InputNumber
                value={scheduleForm.max_retries}
                onChange={(v) =>
                  setScheduleForm({
                    ...scheduleForm,
                    max_retries: v ?? 3,
                  })
                }
                min={0}
                max={10}
                style={{ width: "100%" }}
              />
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* 预览对话框 */}
      <Modal
        open={previewOpen}
        title="同步预览"
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={800}
      >
        {previewLoading ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin tip="加载中..." />
          </div>
        ) : previewData ? (
          <div>
            <Space style={{ marginBottom: 16 }}>
              <Tag
                color={previewData.can_sync ? "success" : "error"}
              >
                {previewData.can_sync ? "可同步" : "不可同步"}
              </Tag>
              <Tag>{modeLabel[previewData.mode] || previewData.mode}</Tag>
              <Text>总行数：{previewData.total_rows}</Text>
            </Space>

            {previewData.error_message && (
              <Alert
                type="error"
                message={previewData.error_message}
                style={{ marginBottom: 16 }}
              />
            )}

            <div style={{ marginBottom: 16 }}>
              <Text strong>目标字段：</Text>
              <Space wrap style={{ marginTop: 4 }}>
                {previewData.target_fields.map((f) => (
                  <Tag key={f} color="blue">
                    {f}
                  </Tag>
                ))}
              </Space>
            </div>

            <div style={{ marginBottom: 16 }}>
              <Text strong>主键字段：</Text>
              <Space wrap style={{ marginTop: 4 }}>
                {previewData.pk_fields.length > 0 ? (
                  previewData.pk_fields.map((f) => (
                    <Tag key={f} color="orange">
                      {f}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary">无</Text>
                )}
              </Space>
            </div>

            <Text strong>采样数据（最多 5 行）：</Text>
            <Table
              size="small"
              columns={previewSampleColumns}
              dataSource={previewData.sample_rows.map((row, idx) => ({
                ...row,
                key: idx,
              }))}
              pagination={false}
              scroll={{ x: true }}
              style={{ marginTop: 8 }}
              locale={{ emptyText: "无采样数据" }}
            />
          </div>
        ) : null}
      </Modal>

      {/* 批量触发对话框 */}
      <Modal
        open={batchOpen}
        title="批量触发同步"
        onCancel={() => setBatchOpen(false)}
        footer={
          batchResult ? (
            <Button type="primary" onClick={() => setBatchOpen(false)}>
              关闭
            </Button>
          ) : (
            <Space>
              <Button onClick={() => setBatchOpen(false)}>取消</Button>
              <Button
                type="primary"
                loading={batchLoading}
                onClick={handleBatchTrigger}
              >
                确认执行
              </Button>
            </Space>
          )
        }
        width={700}
      >
        {batchResult ? (
          <div>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}>
                <Statistic title="总计" value={batchResult.total} />
              </Col>
              <Col span={6}>
                <Statistic
                  title="成功"
                  value={batchResult.succeeded}
                  valueStyle={{ color: "#52c41a" }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="失败"
                  value={batchResult.failed}
                  valueStyle={{ color: "#ff4d4f" }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="跳过"
                  value={batchResult.skipped}
                  valueStyle={{ color: "#faad14" }}
                />
              </Col>
            </Row>
            {batchResult.results.length > 0 && (
              <Table
                size="small"
                rowKey="log_id"
                dataSource={batchResult.results}
                pagination={false}
                columns={[
                  {
                    title: "状态",
                    dataIndex: "status",
                    width: 80,
                    render: (s: string) => (
                      <Tag color={s === "success" ? "success" : "error"}>
                        {s === "success" ? "成功" : "失败"}
                      </Tag>
                    ),
                  },
                  {
                    title: "模式",
                    dataIndex: "mode",
                    width: 80,
                    render: (m: string) => (
                      <Tag>{modeLabel[m] || m}</Tag>
                    ),
                  },
                  {
                    title: "读取",
                    dataIndex: "rows_read",
                    width: 60,
                    align: "right" as const,
                  },
                  {
                    title: "写入",
                    dataIndex: "rows_written",
                    width: 60,
                    align: "right" as const,
                  },
                  {
                    title: "跳过",
                    dataIndex: "rows_skipped",
                    width: 60,
                    align: "right" as const,
                  },
                  {
                    title: "错误",
                    dataIndex: "error_message",
                    ellipsis: true,
                    render: (v: string) => v || "-",
                  },
                ]}
              />
            )}
          </div>
        ) : (
          <div>
            <Alert
              type="info"
              message={`将批量触发 ${selectedIds.length} 个同步配置`}
              style={{ marginBottom: 16 }}
            />
            <Form layout="vertical">
              <Form.Item label="强制全量同步">
                <Switch
                  checked={batchForceFull}
                  onChange={setBatchForceFull}
                />
              </Form.Item>
              <Form.Item label="出错时停止">
                <Switch
                  checked={batchStopOnError}
                  onChange={setBatchStopOnError}
                />
              </Form.Item>
            </Form>
          </div>
        )}
      </Modal>
    </div>
  );
}
