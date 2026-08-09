import { useEffect, useState } from "react";
import {
  Table,
  Tag,
  Button,
  Modal,
  Input,
  Select,
  Space,
  message,
  Typography,
  Form,
  Popconfirm,
  DatePicker,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  CopyOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import {
  listApiTokens,
  createApiToken,
  revokeApiToken,
  rotateApiToken,
} from "@/api/tokens";
import type {
  ApiTokenCreate,
  ApiTokenListItem,
  ApiTokenScope,
} from "@/types";

const { Text, Paragraph } = Typography;

// 可授权 scope 选项
const SCOPE_OPTIONS: { value: ApiTokenScope; label: string }[] = [
  { value: "datasets:read", label: "datasets:read（数据集只读查询）" },
  { value: "datasets:write", label: "datasets:write（数据集写入）" },
  { value: "sync:trigger", label: "sync:trigger（触发同步/爬取）" },
];

// 创建表单值
interface TokenFormValues {
  name: string;
  scopes: ApiTokenScope[];
  expires_at?: dayjs.Dayjs | null;
}

// 明文展示 Modal 状态
interface PlaintextState {
  open: boolean;
  title: string;
  token: string;
}

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// API Token 管理页（admin 专用）：列表 + 创建/吊销/轮换 + 明文一次性展示
const Tokens = () => {
  const [list, setList] = useState<ApiTokenListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<TokenFormValues>();

  // 明文展示 Modal（创建/轮换共用）
  const [plaintext, setPlaintext] = useState<PlaintextState>({
    open: false,
    title: "",
    token: "",
  });

  const loadList = async () => {
    setLoading(true);
    try {
      const data = await listApiTokens();
      setList(data.items);
    } catch (err) {
      message.error(errMsg(err, "加载 Token 列表失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadList();
  }, []);

  // 打开创建 Modal
  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({ scopes: [] });
    setCreateOpen(true);
  };

  // 提交创建
  const handleCreate = async () => {
    let values: TokenFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      const payload: ApiTokenCreate = {
        name: values.name,
        scopes: values.scopes,
        expires_at: values.expires_at
          ? values.expires_at.format("YYYY-MM-DDTHH:mm:ss")
          : null,
      };
      const created = await createApiToken(payload);
      setCreateOpen(false);
      setPlaintext({
        open: true,
        title: `新 Token - ${created.name}`,
        token: created.token,
      });
      void loadList();
    } catch (err) {
      message.error(errMsg(err, "创建 Token 失败"));
    } finally {
      setSubmitting(false);
    }
  };

  // 吊销
  const handleRevoke = async (id: number) => {
    try {
      await revokeApiToken(id);
      message.success("已吊销");
      void loadList();
    } catch (err) {
      message.error(errMsg(err, "吊销失败"));
    }
  };

  // 轮换
  const handleRotate = async (id: number) => {
    try {
      const rotated = await rotateApiToken(id);
      setPlaintext({
        open: true,
        title: `新明文 - ${rotated.name}`,
        token: rotated.token,
      });
      void loadList();
    } catch (err) {
      message.error(errMsg(err, "轮换失败"));
    }
  };

  // 复制到剪贴板
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(plaintext.token);
      message.success("已复制到剪贴板");
    } catch {
      message.error("复制失败，请手动选择并复制");
    }
  };

  const columns: ColumnsType<ApiTokenListItem> = [
    { title: "名称", dataIndex: "name", width: 160 },
    {
      title: "前缀",
      dataIndex: "prefix",
      width: 120,
      render: (v: string) => <Text code>{v}…</Text>,
    },
    {
      title: "Scopes",
      dataIndex: "scopes",
      width: 280,
      render: (scopes: ApiTokenScope[]) =>
        scopes && scopes.length ? (
          <Space wrap size={[4, 4]}>
            {scopes.map((s) => (
              <Tag key={s} color="blue">
                {s}
              </Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">无</Text>
        ),
    },
    {
      title: "过期时间",
      dataIndex: "expires_at",
      width: 180,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString("zh-CN") : "永久有效",
    },
    {
      title: "最近使用",
      dataIndex: "last_used_at",
      width: 180,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString("zh-CN") : "—",
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      render: (active: boolean) =>
        active ? (
          <Tag color="green">启用</Tag>
        ) : (
          <Tag color="default">已吊销</Tag>
        ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      key: "action",
      width: 220,
      render: (_, record) => (
        <Space>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => handleRotate(record.id)}
            disabled={!record.is_active}
          >
            轮换
          </Button>
          <Popconfirm
            title="确认吊销该 Token？吊销后无法恢复，但可通过轮换生成新明文。"
            okText="吊销"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => handleRevoke(record.id)}
            disabled={!record.is_active}
          >
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              disabled={!record.is_active}
            >
              吊销
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div
        style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}
      >
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建 Token
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: "暂无 API Token" }}
      />
      <Modal
        title="新建 API Token"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        destroyOnClose
        width={560}
        footer={[
          <Button key="cancel" onClick={() => setCreateOpen(false)}>
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            loading={submitting}
            onClick={handleCreate}
          >
            创建
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入 Token 名称" }]}
          >
            <Input placeholder="如 dataset-readonly" />
          </Form.Item>
          <Form.Item
            name="scopes"
            label="授权 Scope"
            rules={[{ required: true, message: "至少选择一个 scope" }]}
          >
            <Select
              mode="multiple"
              options={SCOPE_OPTIONS}
              placeholder="选择该 Token 可访问的能力"
            />
          </Form.Item>
          <Form.Item
            name="expires_at"
            label="过期时间（可空，空表示永久有效）"
          >
            <DatePicker
              showTime
              style={{ width: "100%" }}
              placeholder="选择过期时间"
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={plaintext.title}
        open={plaintext.open}
        onCancel={() => setPlaintext({ ...plaintext, open: false })}
        footer={[
          <Button
            key="copy"
            icon={<CopyOutlined />}
            onClick={handleCopy}
          >
            复制
          </Button>,
          <Button
            key="close"
            type="primary"
            onClick={() => setPlaintext({ ...plaintext, open: false })}
          >
            我已保存
          </Button>,
        ]}
        width={640}
        destroyOnClose
      >
        <Paragraph type="warning">
          以下明文 Token 仅此一次展示，关闭后将无法再次查看。请立即复制并安全保存。
        </Paragraph>
        <Paragraph copyable code style={{ wordBreak: "break-all" }}>
          {plaintext.token}
        </Paragraph>
      </Modal>
    </>
  );
};

export default Tokens;
