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
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  HistoryOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import {
  listWebhookSubscriptions,
  createWebhookSubscription,
  updateWebhookSubscription,
  deleteWebhookSubscription,
  listWebhookDeliveries,
  redeliverWebhookDelivery,
} from "@/api/webhooks";
import type {
  WebhookSubscription,
  WebhookSubscriptionCreate,
  WebhookSubscriptionUpdate,
  WebhookDeliveryLog,
  WebhookDeliveryLogList,
} from "@/types";

const { Text, Paragraph } = Typography;

// 可订阅事件类型选项
const EVENT_OPTIONS = [
  { value: "sync.completed", label: "sync.completed（同步完成）" },
  { value: "ingest.completed", label: "ingest.completed（爬取完成）" },
];

// 表单值类型
interface WebhookFormValues {
  name: string;
  url: string;
  secret: string;
  events: string[];
  is_active?: boolean;
}

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// Webhook 订阅管理页（admin 专用）：列表 + 创建/编辑/删除 + 投递日志查看
const Webhooks = () => {
  const [list, setList] = useState<WebhookSubscription[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<WebhookSubscription | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<WebhookFormValues>();

  // 投递日志 Modal 状态
  const [deliveryOpen, setDeliveryOpen] = useState(false);
  const [deliverySub, setDeliverySub] = useState<WebhookSubscription | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDeliveryLog[]>([]);
  const [deliveryTotal, setDeliveryTotal] = useState(0);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [redeliveringId, setRedeliveringId] = useState<number | null>(null);

  const loadList = async () => {
    setLoading(true);
    try {
      const data = await listWebhookSubscriptions();
      setList(data.items);
    } catch (err) {
      message.error(errMsg(err, "加载 Webhook 订阅列表失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadList();
  }, []);

  // 切换启用状态
  const handleToggleActive = async (record: WebhookSubscription) => {
    try {
      const updated = await updateWebhookSubscription(record.id, {
        is_active: !record.is_active,
      });
      setList((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      message.success(updated.is_active ? "已启用" : "已禁用");
    } catch (err) {
      message.error(errMsg(err, "操作失败"));
    }
  };

  // 删除订阅
  const handleDelete = async (id: number) => {
    try {
      await deleteWebhookSubscription(id);
      message.success("已删除");
      void loadList();
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
      events: [],
      secret: "",
    });
    setModalOpen(true);
  };

  // 打开编辑弹窗
  const openEdit = (record: WebhookSubscription) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      url: record.url,
      secret: "", // 编辑时 secret 留空表示不更新
      events: record.events,
      is_active: record.is_active,
    });
    setModalOpen(true);
  };

  // 提交新增/编辑
  const handleSubmit = async () => {
    let values: WebhookFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        const update: WebhookSubscriptionUpdate = {
          name: values.name,
          url: values.url,
          events: values.events,
          is_active: values.is_active ?? true,
        };
        // secret 非空才更新
        if (values.secret) {
          update.secret = values.secret;
        }
        await updateWebhookSubscription(editing.id, update);
        message.success("已更新");
      } else {
        const create: WebhookSubscriptionCreate = {
          name: values.name,
          url: values.url,
          secret: values.secret,
          events: values.events,
          is_active: values.is_active ?? true,
        };
        await createWebhookSubscription(create);
        message.success("已创建");
      }
      setModalOpen(false);
      void loadList();
    } catch (err) {
      message.error(errMsg(err, "保存失败"));
    } finally {
      setSubmitting(false);
    }
  };

  // 查看投递日志
  const openDeliveries = async (record: WebhookSubscription) => {
    setDeliverySub(record);
    setDeliveries([]);
    setDeliveryTotal(0);
    setDeliveryOpen(true);
    setDeliveryLoading(true);
    try {
      const data: WebhookDeliveryLogList = await listWebhookDeliveries(record.id, {
        limit: 50,
      });
      setDeliveries(data.items);
      setDeliveryTotal(data.total);
    } catch (err) {
      message.error(errMsg(err, "加载投递日志失败"));
    } finally {
      setDeliveryLoading(false);
    }
  };

  // 刷新投递日志列表（重投后调用）
  const refreshDeliveries = async () => {
    if (!deliverySub) return;
    setDeliveryLoading(true);
    try {
      const data: WebhookDeliveryLogList = await listWebhookDeliveries(
        deliverySub.id,
        { limit: 50 }
      );
      setDeliveries(data.items);
      setDeliveryTotal(data.total);
    } catch (err) {
      message.error(errMsg(err, "刷新投递日志失败"));
    } finally {
      setDeliveryLoading(false);
    }
  };

  // 重投指定日志
  const handleRedeliver = async (log: WebhookDeliveryLog) => {
    if (!deliverySub) return;
    setRedeliveringId(log.id);
    try {
      await redeliverWebhookDelivery(deliverySub.id, log.id);
      message.success("重投成功");
      await refreshDeliveries();
    } catch (err) {
      message.error(errMsg(err, "重投失败"));
    } finally {
      setRedeliveringId(null);
    }
  };

  const columns: ColumnsType<WebhookSubscription> = [
    { title: "名称", dataIndex: "name", width: 160 },
    {
      title: "接收 URL",
      dataIndex: "url",
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v}>
          <Text style={{ maxWidth: 280 }} ellipsis>
            {v}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "事件类型",
      dataIndex: "events",
      width: 240,
      render: (events: string[]) =>
        events && events.length ? (
          <Space wrap size={[4, 4]}>
            {events.map((e) => (
              <Tag key={e} color="blue">
                {e}
              </Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">无</Text>
        ),
    },
    {
      title: "签名算法",
      dataIndex: "signing_algorithm",
      width: 120,
      render: (v: string) => <Tag>{v}</Tag>,
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
      width: 240,
      render: (_, record) => (
        <Space>
          <Button
            size="small"
            icon={<HistoryOutlined />}
            onClick={() => openDeliveries(record)}
          >
            日志
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除该 Webhook 订阅？"
            okText="删除"
            cancelText="取消"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 投递日志表格列
  const deliveryColumns: ColumnsType<WebhookDeliveryLog> = [
    {
      title: "事件类型",
      dataIndex: "event_type",
      width: 160,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: "状态码",
      dataIndex: "status_code",
      width: 80,
      render: (v: number | null) =>
        v === null ? (
          <Text type="secondary">—</Text>
        ) : v >= 200 && v < 300 ? (
          <Tag color="green">{v}</Tag>
        ) : (
          <Tag color="red">{v}</Tag>
        ),
    },
    {
      title: "重试",
      dataIndex: "retry_count",
      width: 60,
    },
    {
      title: "耗时(ms)",
      dataIndex: "duration_ms",
      width: 90,
      render: (v: number | null) => (v !== null ? v : "—"),
    },
    {
      title: "错误信息",
      dataIndex: "error_message",
      ellipsis: true,
      render: (v: string) =>
        v ? (
          <Tooltip title={v}>
            <Text type="danger" style={{ maxWidth: 200 }} ellipsis>
              {v}
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "—"),
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      render: (_: unknown, record: WebhookDeliveryLog) => {
        const isFailed =
          record.status_code === null ||
          record.status_code < 200 ||
          record.status_code >= 300;
        if (!isFailed) return null;
        return (
          <Popconfirm
            title="确认重投该日志？"
            description="将同步重新投递，可能耗时数秒。"
            okText="重投"
            cancelText="取消"
            onConfirm={() => handleRedeliver(record)}
          >
            <Button
              size="small"
              icon={<RedoOutlined />}
              loading={redeliveringId === record.id}
            >
              重投
            </Button>
          </Popconfirm>
        );
      },
    },
  ];

  return (
    <>
      <div
        style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}
      >
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增订阅
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: "暂无 Webhook 订阅" }}
      />
      <Modal
        title={editing ? `编辑订阅 - ${editing.name}` : "新增 Webhook 订阅"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        width={600}
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
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入订阅名称" }]}
          >
            <Input placeholder="如 user-sync-notify" />
          </Form.Item>
          <Form.Item
            name="url"
            label="接收 URL"
            rules={[
              { required: true, message: "请输入接收 URL" },
              {
                pattern: /^https?:\/\/.+/,
                message: "请输入合法的 HTTP/HTTPS URL",
              },
            ]}
          >
            <Input placeholder="https://example.com/webhook" />
          </Form.Item>
          <Form.Item
            name="secret"
            label={
              editing
                ? "签名密钥（留空表示不更新）"
                : "签名密钥（HMAC-SHA256）"
            }
            rules={
              editing
                ? []
                : [{ required: true, message: "请输入签名密钥" }]
            }
          >
            <Input.Password placeholder="接收方须用此密钥校验签名" />
          </Form.Item>
          <Form.Item
            name="events"
            label="订阅事件类型"
            rules={[{ required: true, message: "至少选择一个事件类型" }]}
          >
            <Select
              mode="multiple"
              options={EVENT_OPTIONS}
              placeholder="选择要订阅的事件"
            />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`投递日志 - ${deliverySub?.name ?? ""}`}
        open={deliveryOpen}
        onCancel={() => setDeliveryOpen(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        <Table
          rowKey="id"
          columns={deliveryColumns}
          dataSource={deliveries}
          loading={deliveryLoading}
          pagination={false}
          scroll={{ x: true }}
          size="small"
          locale={{ emptyText: "暂无投递日志" }}
        />
        {deliveryTotal > 0 && (
          <Paragraph type="secondary" style={{ marginTop: 8 }}>
            共 {deliveryTotal} 条记录（仅展示最近 50 条）
          </Paragraph>
        )}
      </Modal>
    </>
  );
};

export default Webhooks;
