import { useEffect, useState } from "react";
import {
  Table,
  Tag,
  Switch,
  Button,
  Modal,
  Input,
  Select,
  Space,
  message,
  Typography,
  Form,
  Popconfirm,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
} from "@ant-design/icons";
import {
  listDatasets,
  createDataset,
  updateDataset,
  deleteDataset,
  previewDatasetRows,
} from "@/api/datasets";
import { listDatasources } from "@/api/datasources";
import type {
  Dataset,
  DatasetCreate,
  DatasetUpdate,
  DataSource,
  DatasetRows,
} from "@/types";

const { Text, Paragraph } = Typography;

// 表单值类型
interface DatasetFormValues {
  slug: string;
  name: string;
  description?: string;
  datasource_id: number;
  table_name: string;
  schema_name?: string;
  fields_whitelist?: string[];
  filter_expression?: string; // JSON 字符串，便于编辑
  aggregations?: string; // JSON 字符串
  is_active?: boolean;
}

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// 安全解析 JSON 字符串；空串/空白返回空对象
const parseJsonOrEmpty = (s: string | undefined): Record<string, unknown> => {
  if (!s || !s.trim()) return {};
  try {
    return JSON.parse(s) as Record<string, unknown>;
  } catch {
    throw new Error("JSON 格式错误");
  }
};

// 数据集管理页（admin 专用）：列表 + 创建/编辑/删除 + 预览
const Datasets = () => {
  const [list, setList] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Dataset | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [form] = Form.useForm<DatasetFormValues>();

  // 预览 Modal 状态
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewSlug, setPreviewSlug] = useState<string>("");
  const [previewData, setPreviewData] = useState<DatasetRows | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const loadDatasets = async () => {
    setLoading(true);
    try {
      const data = await listDatasets();
      setList(data.items);
    } catch (err) {
      message.error(errMsg(err, "加载数据集列表失败"));
    } finally {
      setLoading(false);
    }
  };

  const loadDatasources = async () => {
    try {
      const data = await listDatasources();
      setDatasources(data);
    } catch (err) {
      message.error(errMsg(err, "加载数据源列表失败"));
    }
  };

  useEffect(() => {
    void loadDatasets();
    void loadDatasources();
  }, []);

  // 切换启用状态
  const handleToggleActive = async (record: Dataset) => {
    try {
      const updated = await updateDataset(record.slug, {
        is_active: !record.is_active,
      });
      setList((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      message.success(updated.is_active ? "已启用" : "已禁用");
    } catch (err) {
      message.error(errMsg(err, "操作失败"));
    }
  };

  // 删除数据集
  const handleDelete = async (slug: string) => {
    try {
      await deleteDataset(slug);
      message.success("已删除");
      void loadDatasets();
    } catch (err) {
      message.error(errMsg(err, "删除失败"));
    }
  };

  // 打开新增弹窗
  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      is_active: true,
      fields_whitelist: [],
      filter_expression: "",
      aggregations: "",
    });
    setModalOpen(true);
  };

  // 打开编辑弹窗
  const openEdit = (record: Dataset) => {
    setEditing(record);
    form.setFieldsValue({
      slug: record.slug,
      name: record.name,
      description: record.description,
      datasource_id: record.datasource_id,
      table_name: record.table_name,
      schema_name: record.schema_name,
      fields_whitelist: record.fields_whitelist,
      filter_expression: JSON.stringify(record.filter_expression, null, 2),
      aggregations: JSON.stringify(record.aggregations, null, 2),
      is_active: record.is_active,
    });
    setModalOpen(true);
  };

  // 提交新增/编辑
  const handleSubmit = async () => {
    let values: DatasetFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    let filterExpr: Record<string, unknown>;
    let aggregations: Record<string, unknown>;
    try {
      filterExpr = parseJsonOrEmpty(values.filter_expression);
      aggregations = parseJsonOrEmpty(values.aggregations);
    } catch (e) {
      message.error((e as Error).message);
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        const update: DatasetUpdate = {
          slug: values.slug,
          name: values.name,
          description: values.description ?? "",
          datasource_id: values.datasource_id,
          table_name: values.table_name,
          schema_name: values.schema_name ?? "",
          fields_whitelist: values.fields_whitelist ?? [],
          filter_expression: filterExpr,
          aggregations: aggregations,
          is_active: values.is_active ?? true,
        };
        await updateDataset(editing.slug, update);
        message.success("已更新");
      } else {
        const create: DatasetCreate = {
          slug: values.slug,
          name: values.name,
          description: values.description ?? "",
          datasource_id: values.datasource_id,
          table_name: values.table_name,
          schema_name: values.schema_name ?? "",
          fields_whitelist: values.fields_whitelist ?? [],
          filter_expression: filterExpr,
          aggregations: aggregations,
          is_active: values.is_active ?? true,
        };
        await createDataset(create);
        message.success("已创建");
      }
      setModalOpen(false);
      void loadDatasets();
    } catch (err) {
      message.error(errMsg(err, "保存失败"));
    } finally {
      setSubmitting(false);
    }
  };

  // 打开预览
  const openPreview = async (record: Dataset) => {
    setPreviewSlug(record.slug);
    setPreviewData(null);
    setPreviewOpen(true);
    setPreviewLoading(true);
    try {
      const data = await previewDatasetRows(record.slug, { page_size: 20 });
      setPreviewData(data);
    } catch (err) {
      message.error(errMsg(err, "预览失败"));
    } finally {
      setPreviewLoading(false);
    }
  };

  const columns: ColumnsType<Dataset> = [
    { title: "Slug", dataIndex: "slug", width: 160 },
    { title: "名称", dataIndex: "name" },
    {
      title: "数据源",
      dataIndex: "datasource_id",
      width: 140,
      render: (id: number) => {
        const ds = datasources.find((d) => d.id === id);
        return ds ? ds.name : <Text type="secondary">#{id}</Text>;
      },
    },
    { title: "表名", dataIndex: "table_name", width: 140 },
    {
      title: "白名单",
      dataIndex: "fields_whitelist",
      width: 180,
      render: (cols: string[]) =>
        cols && cols.length ? (
          <Space wrap size={[4, 4]}>
            {cols.map((c) => (
              <Tag key={c}>{c}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">全部</Text>
        ),
    },
    {
      title: "版本",
      dataIndex: "version",
      width: 80,
      render: (v: number) => <Tag color="blue">v{v}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      render: (active: boolean, record) => (
        <Switch checked={active} onChange={() => handleToggleActive(record)} />
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "—"),
    },
    {
      title: "操作",
      key: "action",
      width: 220,
      render: (_, record) => (
        <Space>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => openPreview(record)}
          >
            预览
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除该数据集？"
            okText="删除"
            cancelText="取消"
            onConfirm={() => handleDelete(record.slug)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 预览结果表格列
  const previewColumns =
    previewData?.columns.map((col) => ({
      title: col,
      dataIndex: col,
      ellipsis: true,
      render: (v: unknown) => (v === null || v === undefined ? "—" : String(v)),
    })) ?? [];

  const datasourceOptions = datasources.map((d) => ({
    value: d.id,
    label: `${d.name} (${d.engine})`,
  }));

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增数据集
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: "暂无数据集" }}
      />
      <Modal
        title={editing ? `编辑数据集 - ${editing.slug}` : "新增数据集"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        width={640}
        footer={[
          <Button key="cancel" onClick={() => setModalOpen(false)}>
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
          >
            {editing ? "保存" : "创建"}
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="slug"
            label="Slug"
            rules={[
              { required: true, message: "请输入 Slug" },
              {
                pattern: /^[a-z0-9-]+$/,
                message: "仅允许小写字母、数字与连字符",
              },
            ]}
          >
            <Input placeholder="如 user-profiles" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="请输入数据集名称" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
          <Form.Item
            name="datasource_id"
            label="数据源"
            rules={[{ required: true, message: "请选择数据源" }]}
          >
            <Select options={datasourceOptions} placeholder="请选择数据源" />
          </Form.Item>
          <Form.Item
            name="table_name"
            label="表名"
            rules={[{ required: true, message: "请输入表名" }]}
          >
            <Input placeholder="如 users" />
          </Form.Item>
          <Form.Item name="schema_name" label="Schema 名（SQLite 留空）">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="fields_whitelist" label="字段白名单（空表示全部）">
            <Select
              mode="tags"
              placeholder="输入列名后回车添加"
              tokenSeparators={[",", " "]}
            />
          </Form.Item>
          <Form.Item
            name="filter_expression"
            label="行级过滤条件（JSON）"
            tooltip='简写 {"col": val} 或标准 {"col": {"op": "eq", "val": val}}'
          >
            <Input.TextArea rows={3} placeholder='如 {"is_active": 1}' />
          </Form.Item>
          <Form.Item name="aggregations" label="预聚合规则（JSON）">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`预览数据集 - ${previewSlug}`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={900}
        destroyOnClose
      >
        {previewData && previewData.total === 0 ? (
          <Paragraph type="secondary">无数据</Paragraph>
        ) : (
          <Table
            rowKey={(_, idx) => String(idx)}
            columns={previewColumns}
            dataSource={previewData?.items ?? []}
            loading={previewLoading}
            pagination={false}
            scroll={{ x: true }}
            size="small"
          />
        )}
        {previewData && (
          <Paragraph type="secondary" style={{ marginTop: 8 }}>
            共 {previewData.total} 行
          </Paragraph>
        )}
      </Modal>
    </>
  );
};

export default Datasets;
