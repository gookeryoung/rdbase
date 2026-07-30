import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Layout,
  Table,
  Button,
  Space,
  Typography,
  Modal,
  Input,
  Select,
  InputNumber,
  Switch,
  Tag,
  Popconfirm,
  message,
  Tabs,
  Empty,
  Tooltip,
  List,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SaveOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  HistoryOutlined,
  RollbackOutlined,
} from "@ant-design/icons";
import {
  listDrafts,
  createDraft,
  updateDraft,
  deleteDraft,
  listVersions,
  rollbackToVersion,
  previewDDL,
  applyDraft,
} from "@/api/designer";
import { listDatasources } from "@/api/datasources";
import { useAuthStore } from "@/store/auth";
import { isDesignerOrAdmin } from "@/utils/permission";
import type {
  DataSource,
  Draft,
  DraftStatus,
  FieldSpec,
  ForeignKeySpec,
  IndexSpec,
  TableDesignSpec,
  Version,
  EngineType,
} from "@/types";

const { Sider, Content } = Layout;
const { Text, Title } = Typography;

// 通用字段类型选项
const fieldTypeOptions = [
  "INTEGER",
  "BIGINT",
  "SMALLINT",
  "VARCHAR",
  "CHAR",
  "TEXT",
  "BLOB",
  "DATE",
  "TIME",
  "DATETIME",
  "TIMESTAMP",
  "BOOLEAN",
  "DECIMAL",
  "FLOAT",
  "DOUBLE",
  "JSON",
  "SERIAL",
  "BIGSERIAL",
].map((t) => ({ value: t, label: t }));

// ON DELETE 行为选项
const onDeleteOptions = [
  { value: "CASCADE", label: "CASCADE" },
  { value: "SET NULL", label: "SET NULL" },
  { value: "RESTRICT", label: "RESTRICT" },
  { value: "NO ACTION", label: "NO ACTION" },
  { value: "SET DEFAULT", label: "SET DEFAULT" },
];

// 草稿状态颜色映射
const statusColor: Record<DraftStatus, string> = {
  draft: "default",
  applied: "green",
  archived: "gray",
};

// 草稿状态中文标签
const statusLabel: Record<DraftStatus, string> = {
  draft: "草稿",
  applied: "已应用",
  archived: "已归档",
};

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// 引擎中文标签
const engineLabel: Record<EngineType, string> = {
  mysql: "MySQL",
  postgresql: "PostgreSQL",
  sqlite: "SQLite",
};

// 创建默认字段（自增主键）
const makeDefaultIdField = (): FieldSpec => ({
  name: "id",
  type: "INTEGER",
  length: null,
  nullable: false,
  default: null,
  comment: null,
  primary_key: true,
  unique: false,
  autoincrement: true,
});

// 创建空字段
const makeEmptyField = (): FieldSpec => ({
  name: "",
  type: "VARCHAR",
  length: 255,
  nullable: true,
  default: null,
  comment: null,
  primary_key: false,
  unique: false,
  autoincrement: false,
});

// 创建空索引
const makeEmptyIndex = (): IndexSpec => ({
  name: "",
  columns: [],
  unique: false,
});

// 创建空外键
const makeEmptyForeignKey = (): ForeignKeySpec => ({
  name: null,
  columns: [],
  referred_table: "",
  referred_columns: [],
  on_delete: "RESTRICT",
});

// 创建草稿表单值
interface DraftFormValues {
  name: string;
  datasource_id: number;
  table_name: string;
  schema_name?: string;
}

// 数据库设计页：左侧草稿列表，右侧表设计器
const Designer = () => {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // 当前编辑中的 spec（本地状态，保存时写回后端）
  const [editingSpec, setEditingSpec] = useState<TableDesignSpec | null>(null);
  const [editingName, setEditingName] = useState("");
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [ddlStatements, setDdlStatements] = useState<string[] | null>(null);
  const [ddlLoading, setDdlLoading] = useState(false);
  const [versions, setVersions] = useState<Version[]>([]);
  const [activeTab, setActiveTab] = useState("fields");

  const user = useAuthStore((state) => state.user);
  const canEdit = isDesignerOrAdmin(user);

  // 加载数据源列表
  const loadDatasources = useCallback(async () => {
    try {
      const data = await listDatasources();
      setDatasources(data);
    } catch (err) {
      message.error(errMsg(err, "加载数据源失败"));
    }
  }, []);

  // 加载草稿列表
  const loadDrafts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listDrafts();
      setDrafts(data);
    } catch (err) {
      message.error(errMsg(err, "加载草稿列表失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  // 加载版本列表
  const loadVersions = useCallback(async (draftId: number) => {
    try {
      const data = await listVersions(draftId);
      setVersions(data);
    } catch (err) {
      message.error(errMsg(err, "加载版本列表失败"));
    }
  }, []);

  useEffect(() => {
    void loadDatasources();
    void loadDrafts();
  }, [loadDatasources, loadDrafts]);

  // 选中草稿时初始化编辑状态
  const selectedDraft = useMemo(
    () => drafts.find((d) => d.id === selectedId) ?? null,
    [drafts, selectedId]
  );

  useEffect(() => {
    if (selectedDraft) {
      setEditingSpec(structuredClone(selectedDraft.spec));
      setEditingName(selectedDraft.name);
      setDdlStatements(null);
      void loadVersions(selectedDraft.id);
    } else {
      setEditingSpec(null);
      setEditingName("");
      setDdlStatements(null);
      setVersions([]);
    }
  }, [selectedDraft, loadVersions]);

  // 数据源 ID → DataSource 映射
  const dsMap = useMemo(() => {
    const m = new Map<number, DataSource>();
    datasources.forEach((d) => m.set(d.id, d));
    return m;
  }, [datasources]);

  // 当前草稿对应的引擎
  const currentEngine: EngineType | null = selectedDraft
    ? dsMap.get(selectedDraft.datasource_id)?.engine ?? null
    : null;

  // ----- 字段编辑 -----
  const updateField = (idx: number, patch: Partial<FieldSpec>) => {
    if (!editingSpec) return;
    const fields = [...editingSpec.fields];
    fields[idx] = { ...fields[idx], ...patch };
    setEditingSpec({ ...editingSpec, fields });
    setDdlStatements(null);
  };

  const addField = () => {
    if (!editingSpec) return;
    setEditingSpec({
      ...editingSpec,
      fields: [...editingSpec.fields, makeEmptyField()],
    });
    setDdlStatements(null);
  };

  const removeField = (idx: number) => {
    if (!editingSpec) return;
    const fields = editingSpec.fields.filter((_, i) => i !== idx);
    setEditingSpec({ ...editingSpec, fields });
    setDdlStatements(null);
  };

  // ----- 索引编辑 -----
  const updateIndex = (idx: number, patch: Partial<IndexSpec>) => {
    if (!editingSpec) return;
    const indexes = [...editingSpec.indexes];
    indexes[idx] = { ...indexes[idx], ...patch };
    setEditingSpec({ ...editingSpec, indexes });
    setDdlStatements(null);
  };

  const addIndex = () => {
    if (!editingSpec) return;
    setEditingSpec({
      ...editingSpec,
      indexes: [...editingSpec.indexes, makeEmptyIndex()],
    });
    setDdlStatements(null);
  };

  const removeIndex = (idx: number) => {
    if (!editingSpec) return;
    const indexes = editingSpec.indexes.filter((_, i) => i !== idx);
    setEditingSpec({ ...editingSpec, indexes });
    setDdlStatements(null);
  };

  // ----- 外键编辑 -----
  const updateForeignKey = (idx: number, patch: Partial<ForeignKeySpec>) => {
    if (!editingSpec) return;
    const next = [...editingSpec.foreign_keys];
    next[idx] = { ...next[idx], ...patch };
    setEditingSpec({ ...editingSpec, foreign_keys: next });
    setDdlStatements(null);
  };

  const addForeignKey = () => {
    if (!editingSpec) return;
    setEditingSpec({
      ...editingSpec,
      foreign_keys: [...editingSpec.foreign_keys, makeEmptyForeignKey()],
    });
    setDdlStatements(null);
  };

  const removeForeignKey = (idx: number) => {
    if (!editingSpec) return;
    const next = editingSpec.foreign_keys.filter((_, i) => i !== idx);
    setEditingSpec({ ...editingSpec, foreign_keys: next });
    setDdlStatements(null);
  };

  // ----- 保存草稿 -----
  const handleSave = async () => {
    if (!selectedDraft || !editingSpec) return;
    if (!editingSpec.fields.length || editingSpec.fields.some((f) => !f.name)) {
      message.warning("字段名不能为空且至少需要一个字段");
      return;
    }
    setSaving(true);
    try {
      // 同步表名到 spec.name
      const spec: TableDesignSpec = {
        ...editingSpec,
        name: selectedDraft.table_name,
      };
      const updated = await updateDraft(selectedDraft.id, {
        name: editingName,
        spec,
      });
      setDrafts((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      setDdlStatements(null);
      void loadVersions(updated.id);
      message.success("已保存");
    } catch (err) {
      message.error(errMsg(err, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  // ----- 应用到数据源 -----
  const handleApply = async () => {
    if (!selectedDraft || !editingSpec) return;
    setApplying(true);
    try {
      const result = await applyDraft(selectedDraft.id, {});
      message.success(`已执行 ${result.executed ?? 0} 条 DDL 语句`);
      // 刷新草稿状态
      const refreshed = await listDrafts();
      setDrafts(refreshed);
    } catch (err) {
      message.error(errMsg(err, "应用失败"));
    } finally {
      setApplying(false);
    }
  };

  // ----- DDL 预览 -----
  const handlePreviewDDL = async () => {
    if (!selectedDraft || !editingSpec) return;
    setDdlLoading(true);
    try {
      const spec: TableDesignSpec = {
        ...editingSpec,
        name: selectedDraft.table_name,
      };
      const result = await previewDDL({
        datasource_id: selectedDraft.datasource_id,
        spec,
      });
      setDdlStatements(result.statements);
    } catch (err) {
      message.error(errMsg(err, "生成 DDL 失败"));
    } finally {
      setDdlLoading(false);
    }
  };

  // ----- 删除草稿 -----
  const handleDelete = async (id: number) => {
    try {
      await deleteDraft(id);
      message.success("已删除");
      if (selectedId === id) setSelectedId(null);
      void loadDrafts();
    } catch (err) {
      message.error(errMsg(err, "删除失败"));
    }
  };

  // ----- 回滚版本 -----
  const handleRollback = async (versionNo: number) => {
    if (!selectedDraft) return;
    try {
      const updated = await rollbackToVersion(selectedDraft.id, versionNo);
      setDrafts((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      setEditingSpec(structuredClone(updated.spec));
      void loadVersions(updated.id);
      message.success(`已回滚到 v${versionNo}`);
    } catch (err) {
      message.error(errMsg(err, "回滚失败"));
    }
  };

  // ----- 创建草稿 -----
  const [createForm, setCreateForm] = useState<DraftFormValues>({
    name: "",
    datasource_id: 0,
    table_name: "",
    schema_name: "",
  });

  const openCreate = () => {
    setCreateForm({
      name: "",
      datasource_id: datasources[0]?.id ?? 0,
      table_name: "",
      schema_name: "",
    });
    setCreateOpen(true);
  };

  const handleCreate = async () => {
    if (!createForm.name || !createForm.datasource_id || !createForm.table_name) {
      message.warning("请填写名称、数据源与表名");
      return;
    }
    setCreating(true);
    try {
      const spec: TableDesignSpec = {
        name: createForm.table_name,
        schema_name: createForm.schema_name || null,
        comment: null,
        fields: [makeDefaultIdField()],
        indexes: [],
        foreign_keys: [],
      };
      const draft = await createDraft({
        name: createForm.name,
        datasource_id: createForm.datasource_id,
        table_name: createForm.table_name,
        schema_name: createForm.schema_name || null,
        spec,
      });
      setDrafts((prev) => [draft, ...prev]);
      setSelectedId(draft.id);
      setCreateOpen(false);
      message.success("已创建");
    } catch (err) {
      message.error(errMsg(err, "创建失败"));
    } finally {
      setCreating(false);
    }
  };

  // ----- 字段表格列定义 -----
  const fieldColumns: ColumnsType<FieldSpec> = [
    {
      title: "字段名",
      dataIndex: "name",
      width: 140,
      render: (_, record, idx) => (
        <Input
          value={record.name}
          placeholder="字段名"
          disabled={!canEdit}
          onChange={(e) => updateField(idx, { name: e.target.value })}
        />
      ),
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 140,
      render: (_, record, idx) => (
        <Select
          value={record.type}
          options={fieldTypeOptions}
          disabled={!canEdit}
          showSearch
          onChange={(v) => updateField(idx, { type: v })}
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "长度",
      dataIndex: "length",
      width: 90,
      render: (_, record, idx) => (
        <InputNumber
          value={record.length ?? undefined}
          placeholder="—"
          min={1}
          disabled={!canEdit}
          onChange={(v) => updateField(idx, { length: v ?? null })}
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "可空",
      dataIndex: "nullable",
      width: 60,
      render: (_, record, idx) => (
        <Switch
          checked={record.nullable}
          disabled={!canEdit}
          onChange={(v) => updateField(idx, { nullable: v })}
        />
      ),
    },
    {
      title: "默认值",
      dataIndex: "default",
      width: 130,
      render: (_, record, idx) => (
        <Input
          value={record.default ?? ""}
          placeholder="如 0、'x'、CURRENT_TIMESTAMP"
          disabled={!canEdit}
          onChange={(e) =>
            updateField(idx, { default: e.target.value || null })
          }
        />
      ),
    },
    {
      title: "注释",
      dataIndex: "comment",
      width: 130,
      render: (_, record, idx) => (
        <Input
          value={record.comment ?? ""}
          placeholder="注释"
          disabled={!canEdit}
          onChange={(e) =>
            updateField(idx, { comment: e.target.value || null })
          }
        />
      ),
    },
    {
      title: "主键",
      dataIndex: "primary_key",
      width: 60,
      render: (_, record, idx) => (
        <Switch
          checked={record.primary_key}
          disabled={!canEdit}
          onChange={(v) => updateField(idx, { primary_key: v })}
        />
      ),
    },
    {
      title: "唯一",
      dataIndex: "unique",
      width: 60,
      render: (_, record, idx) => (
        <Switch
          checked={record.unique}
          disabled={!canEdit || record.primary_key}
          onChange={(v) => updateField(idx, { unique: v })}
        />
      ),
    },
    {
      title: "自增",
      dataIndex: "autoincrement",
      width: 60,
      render: (_, record, idx) => (
        <Switch
          checked={record.autoincrement}
          disabled={!canEdit || !record.primary_key}
          onChange={(v) => updateField(idx, { autoincrement: v })}
        />
      ),
    },
    {
      title: "",
      key: "action",
      width: 60,
      render: (_, __, idx) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          disabled={!canEdit}
          onClick={() => removeField(idx)}
        />
      ),
    },
  ];

  // ----- 索引表格列定义 -----
  const indexColumns: ColumnsType<IndexSpec> = [
    {
      title: "索引名",
      dataIndex: "name",
      width: 180,
      render: (_, record, idx) => (
        <Input
          value={record.name}
          placeholder="索引名"
          disabled={!canEdit}
          onChange={(e) => updateIndex(idx, { name: e.target.value })}
        />
      ),
    },
    {
      title: "列",
      dataIndex: "columns",
      render: (_, record, idx) => (
        <Select
          mode="tags"
          value={record.columns}
          placeholder="选择列（可多选）"
          disabled={!canEdit || !editingSpec}
          options={(editingSpec?.fields ?? []).map((f) => ({
            value: f.name,
            label: f.name,
          }))}
          onChange={(v: string[]) => updateIndex(idx, { columns: v })}
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "唯一",
      dataIndex: "unique",
      width: 60,
      render: (_, record, idx) => (
        <Switch
          checked={record.unique}
          disabled={!canEdit}
          onChange={(v) => updateIndex(idx, { unique: v })}
        />
      ),
    },
    {
      title: "",
      key: "action",
      width: 60,
      render: (_, __, idx) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          disabled={!canEdit}
          onClick={() => removeIndex(idx)}
        />
      ),
    },
  ];

  // ----- 外键表格列定义 -----
  const foreignKeyColumns: ColumnsType<ForeignKeySpec> = [
    {
      title: "约束名",
      dataIndex: "name",
      width: 140,
      render: (_, record, idx) => (
        <Input
          value={record.name ?? ""}
          placeholder="留空自动命名"
          disabled={!canEdit}
          onChange={(e) =>
            updateForeignKey(idx, { name: e.target.value || null })
          }
        />
      ),
    },
    {
      title: "列",
      dataIndex: "columns",
      width: 160,
      render: (_, record, idx) => (
        <Select
          mode="tags"
          value={record.columns}
          placeholder="选择列"
          disabled={!canEdit || !editingSpec}
          options={(editingSpec?.fields ?? []).map((f) => ({
            value: f.name,
            label: f.name,
          }))}
          onChange={(v: string[]) => updateForeignKey(idx, { columns: v })}
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "引用表",
      dataIndex: "referred_table",
      width: 140,
      render: (_, record, idx) => (
        <Input
          value={record.referred_table}
          placeholder="引用表名"
          disabled={!canEdit}
          onChange={(e) =>
            updateForeignKey(idx, { referred_table: e.target.value })
          }
        />
      ),
    },
    {
      title: "引用列",
      dataIndex: "referred_columns",
      width: 140,
      render: (_, record, idx) => (
        <Select
          mode="tags"
          value={record.referred_columns}
          placeholder="引用列"
          disabled={!canEdit}
          onChange={(v: string[]) =>
            updateForeignKey(idx, { referred_columns: v })
          }
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "ON DELETE",
      dataIndex: "on_delete",
      width: 130,
      render: (_, record, idx) => (
        <Select
          value={record.on_delete}
          options={onDeleteOptions}
          disabled={!canEdit}
          onChange={(v) => updateForeignKey(idx, { on_delete: v })}
          style={{ width: "100%" }}
        />
      ),
    },
    {
      title: "",
      key: "action",
      width: 60,
      render: (_, __, idx) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          disabled={!canEdit}
          onClick={() => removeForeignKey(idx)}
        />
      ),
    },
  ];

  // 草稿列表列
  const draftColumns: ColumnsType<Draft> = [
    {
      title: "草稿",
      key: "draft",
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{r.name}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.table_name}
            {r.schema_name ? ` (${r.schema_name})` : ""}
          </Text>
        </div>
      ),
    },
    {
      title: "数据源",
      dataIndex: "datasource_id",
      width: 120,
      render: (id: number) => {
        const ds = dsMap.get(id);
        return ds ? (
          <Tag color="blue">{engineLabel[ds.engine]}</Tag>
        ) : (
          <Text type="secondary">—</Text>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (s: DraftStatus) => (
        <Tag color={statusColor[s]}>{statusLabel[s]}</Tag>
      ),
    },
    {
      title: "",
      key: "action",
      width: 80,
      render: (_, r) => (
        <Popconfirm
          title="确认删除该草稿？"
          okText="删除"
          cancelText="取消"
          disabled={!canEdit}
          onConfirm={() => handleDelete(r.id)}
        >
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            disabled={!canEdit}
          />
        </Popconfirm>
      ),
    },
  ];

  return (
    <Layout style={{ minHeight: "calc(100vh - 112px)", background: "transparent" }}>
      <Sider
        width={360}
        theme="light"
        style={{ background: "#fff", marginRight: 16, borderRadius: 8, overflow: "auto" }}
      >
        <div style={{ padding: 12, borderBottom: "1px solid #f0f0f0" }}>
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <Title level={5} style={{ margin: 0 }}>
              草稿列表
            </Title>
            <Space>
              <Tooltip title="刷新">
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => void loadDrafts()}
                />
              </Tooltip>
              {canEdit && (
                <Button
                  size="small"
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={openCreate}
                >
                  新建
                </Button>
              )}
            </Space>
          </Space>
        </div>
        <Table
          rowKey="id"
          columns={draftColumns}
          dataSource={drafts}
          loading={loading}
          size="small"
          pagination={false}
          showHeader={false}
          rowSelection={{
            type: "radio",
            selectedRowKeys: selectedId ? [selectedId] : [],
            onChange: (keys) => setSelectedId(keys[0] as number),
          }}
          onRow={(r) => ({ onClick: () => setSelectedId(r.id) })}
          locale={{ emptyText: <Empty description="暂无草稿" /> }}
        />
      </Sider>
      <Content style={{ background: "#fff", borderRadius: 8, padding: 16, overflow: "auto" }}>
        {!selectedDraft || !editingSpec ? (
          <Empty
            description="请选择左侧草稿或新建草稿"
            style={{ marginTop: 120 }}
          />
        ) : (
          <>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 16,
              }}
            >
              <Space>
                <Input
                  value={editingName}
                  style={{ width: 240 }}
                  disabled={!canEdit}
                  onChange={(e) => setEditingName(e.target.value)}
                />
                <Tag color={statusColor[selectedDraft.status]}>
                  {statusLabel[selectedDraft.status]}
                </Tag>
                {currentEngine && (
                  <Tag color="blue">{engineLabel[currentEngine]}</Tag>
                )}
              </Space>
              <Space>
                {canEdit && (
                  <Button
                    icon={<SaveOutlined />}
                    loading={saving}
                    onClick={handleSave}
                  >
                    保存
                  </Button>
                )}
                {canEdit && (
                  <Popconfirm
                    title="确认应用到目标数据源？将执行 DDL 语句。"
                    okText="应用"
                    cancelText="取消"
                    onConfirm={handleApply}
                  >
                    <Button
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={applying}
                    >
                      应用
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            </div>

            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              items={[
                {
                  key: "fields",
                  label: "字段",
                  children: (
                    <>
                      <Table
                        rowKey={(_, idx) => String(idx)}
                        columns={fieldColumns}
                        dataSource={editingSpec.fields}
                        pagination={false}
                        size="small"
                        scroll={{ x: "max-content" }}
                      />
                      {canEdit && (
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          style={{ marginTop: 12 }}
                          onClick={addField}
                        >
                          添加字段
                        </Button>
                      )}
                    </>
                  ),
                },
                {
                  key: "indexes",
                  label: "索引",
                  children: (
                    <>
                      <Table
                        rowKey={(_, idx) => String(idx)}
                        columns={indexColumns}
                        dataSource={editingSpec.indexes}
                        pagination={false}
                        size="small"
                        scroll={{ x: "max-content" }}
                      />
                      {canEdit && (
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          style={{ marginTop: 12 }}
                          onClick={addIndex}
                        >
                          添加索引
                        </Button>
                      )}
                    </>
                  ),
                },
                {
                  key: "foreign_keys",
                  label: "外键",
                  children: (
                    <>
                      <Table
                        rowKey={(_, idx) => String(idx)}
                        columns={foreignKeyColumns}
                        dataSource={editingSpec.foreign_keys}
                        pagination={false}
                        size="small"
                        scroll={{ x: "max-content" }}
                      />
                      {canEdit && (
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          style={{ marginTop: 12 }}
                          onClick={addForeignKey}
                        >
                          添加外键
                        </Button>
                      )}
                    </>
                  ),
                },
                {
                  key: "ddl",
                  label: (
                    <span>
                      <EyeOutlined /> DDL 预览
                    </span>
                  ),
                  children: (
                    <div>
                      <Button
                        type="primary"
                        icon={<EyeOutlined />}
                        loading={ddlLoading}
                        onClick={handlePreviewDDL}
                        style={{ marginBottom: 12 }}
                      >
                        生成 DDL
                      </Button>
                      {ddlStatements && ddlStatements.length > 0 ? (
                        <pre
                          style={{
                            background: "#f5f5f5",
                            padding: 12,
                            borderRadius: 4,
                            overflow: "auto",
                            fontSize: 13,
                          }}
                        >
                          {ddlStatements.join(";\n")}
                          {ddlStatements.length > 0 ? ";" : ""}
                        </pre>
                      ) : ddlStatements ? (
                        <Empty description="无 DDL 语句（表结构无变更）" />
                      ) : (
                        <Text type="secondary">
                          点击「生成 DDL」预览 CREATE TABLE 语句
                        </Text>
                      )}
                    </div>
                  ),
                },
                {
                  key: "versions",
                  label: (
                    <span>
                      <HistoryOutlined /> 版本
                    </span>
                  ),
                  children: (
                    <List
                      dataSource={versions}
                      renderItem={(v) => (
                        <List.Item
                          actions={
                            canEdit
                              ? [
                                <Popconfirm
                                  key="rollback"
                                  title={`确认回滚到 v${v.version_no}？当前 spec 将被覆盖为新版本。`}
                                  okText="回滚"
                                  cancelText="取消"
                                  onConfirm={() => handleRollback(v.version_no)}
                                >
                                  <Button
                                    size="small"
                                    icon={<RollbackOutlined />}
                                  >
                                    回滚
                                  </Button>
                                </Popconfirm>,
                              ]
                              : undefined
                          }
                        >
                          <List.Item.Meta
                            title={`v${v.version_no}`}
                            description={
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {new Date(v.created_at).toLocaleString("zh-CN")}
                                {" — "}
                                {v.spec.fields.length} 字段
                              </Text>
                            }
                          />
                        </List.Item>
                      )}
                      locale={{ emptyText: <Empty description="暂无版本" /> }}
                    />
                  ),
                },
              ]}
            />
          </>
        )}
      </Content>

      <Modal
        title="新建草稿"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setCreateOpen(false)}>
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            loading={creating}
            onClick={handleCreate}
          >
            创建
          </Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Input
            placeholder="草稿名称（如 users 表设计）"
            value={createForm.name}
            onChange={(e) =>
              setCreateForm((f) => ({ ...f, name: e.target.value }))
            }
          />
          <Select
            placeholder="选择数据源"
            value={createForm.datasource_id || undefined}
            onChange={(v) =>
              setCreateForm((f) => ({ ...f, datasource_id: v }))
            }
            style={{ width: "100%" }}
            options={datasources.map((d) => ({
              value: d.id,
              label: `${d.name} (${engineLabel[d.engine]})`,
            }))}
          />
          <Input
            placeholder="目标表名（如 users）"
            value={createForm.table_name}
            onChange={(e) =>
              setCreateForm((f) => ({ ...f, table_name: e.target.value }))
            }
          />
          <Input
            placeholder="目标 schema（可选，SQLite 忽略）"
            value={createForm.schema_name ?? ""}
            onChange={(e) =>
              setCreateForm((f) => ({ ...f, schema_name: e.target.value }))
            }
          />
        </Space>
      </Modal>
    </Layout>
  );
};

export default Designer;
