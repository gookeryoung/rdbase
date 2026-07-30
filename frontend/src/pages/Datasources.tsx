import { useEffect, useState } from "react";
import {
  Table,
  Tag,
  Switch,
  Button,
  Modal,
  Input,
  InputNumber,
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
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  listDatasources,
  createDatasource,
  updateDatasource,
  deleteDatasource,
  testSavedDatasource,
  testTempConnection,
} from "@/api/datasources";
import { useAuthStore } from "@/store/auth";
import { isAdmin } from "@/utils/permission";
import type {
  DataSource,
  DataSourceCreate,
  DataSourceUpdate,
  EngineType,
  TestConnection,
} from "@/types";

const { Text } = Typography;

// 引擎标签颜色映射：mysql=blue、postgresql=cyan、sqlite=green
const engineColor: Record<EngineType, string> = {
  mysql: "blue",
  postgresql: "cyan",
  sqlite: "green",
};

// 引擎中文标签
const engineLabel: Record<EngineType, string> = {
  mysql: "MySQL",
  postgresql: "PostgreSQL",
  sqlite: "SQLite",
};

// 引擎选项
const engineOptions = (Object.keys(engineLabel) as EngineType[]).map((e) => ({
  value: e,
  label: engineLabel[e],
}));

// 表单值类型
interface DatasourceFormValues {
  name: string;
  engine: EngineType;
  host?: string;
  port?: number | null;
  database: string;
  username?: string;
  password?: string;
  group?: string;
  tags?: string[];
  is_active?: boolean;
}

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// 数据源管理页：列表展示、管理员可增删改、所有登录用户可测试已保存连接
const Datasources = () => {
  const [list, setList] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<DataSource | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [tempTesting, setTempTesting] = useState(false);
  const [form] = Form.useForm<DatasourceFormValues>();
  const engineValue = Form.useWatch("engine", form);

  const user = useAuthStore((state) => state.user);
  const admin = isAdmin(user);

  const loadDatasources = async () => {
    setLoading(true);
    try {
      const data = await listDatasources();
      setList(data);
    } catch (err) {
      message.error(errMsg(err, "加载数据源列表失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDatasources();
  }, []);

  // 切换启用状态（管理员）
  const handleToggleActive = async (record: DataSource) => {
    try {
      const updated = await updateDatasource(record.id, {
        is_active: !record.is_active,
      });
      setList((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      message.success(updated.is_active ? "已启用" : "已禁用");
    } catch (err) {
      message.error(errMsg(err, "操作失败"));
    }
  };

  // 测试已保存数据源连接
  const handleTestSaved = async (record: DataSource) => {
    setTestingId(record.id);
    try {
      const result = await testSavedDatasource(record.id);
      if (result.ok) message.success(result.detail || `${record.name} 连接成功`);
      else message.error(result.detail || `${record.name} 连接失败`);
    } catch (err) {
      message.error(errMsg(err, "测试失败"));
    } finally {
      setTestingId(null);
    }
  };

  // 删除数据源
  const handleDelete = async (id: number) => {
    try {
      await deleteDatasource(id);
      message.success("已删除");
      void loadDatasources();
    } catch (err) {
      message.error(errMsg(err, "删除失败"));
    }
  };

  // 打开新增弹窗
  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      engine: "mysql",
      group: "default",
      tags: [],
      is_active: true,
    });
    setModalOpen(true);
  };

  // 打开编辑弹窗
  const openEdit = (record: DataSource) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      engine: record.engine,
      host: record.host,
      port: record.port,
      database: record.database,
      username: record.username,
      password: undefined,
      group: record.group,
      tags: record.tags,
      is_active: record.is_active,
    });
    setModalOpen(true);
  };

  // 提交新增/编辑
  const handleSubmit = async () => {
    let values: DatasourceFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // 校验失败，表单项自动提示
    }
    const isSqlite = values.engine === "sqlite";
    setSubmitting(true);
    try {
      if (editing) {
        const update: DataSourceUpdate = {
          name: values.name,
          engine: values.engine,
          database: values.database,
          group: values.group || "default",
          tags: values.tags ?? [],
          is_active: values.is_active ?? true,
        };
        if (!isSqlite) {
          update.host = values.host;
          update.port = values.port ?? null;
          update.username = values.username;
          if (values.password) update.password = values.password;
        }
        await updateDatasource(editing.id, update);
        message.success("已更新");
      } else {
        const create: DataSourceCreate = {
          name: values.name,
          engine: values.engine,
          database: values.database,
          group: values.group || "default",
          tags: values.tags ?? [],
        };
        if (!isSqlite) {
          create.host = values.host;
          create.port = values.port ?? null;
          create.username = values.username;
          create.password = values.password;
        }
        await createDatasource(create);
        message.success("已创建");
      }
      setModalOpen(false);
      void loadDatasources();
    } catch (err) {
      message.error(errMsg(err, "保存失败"));
    } finally {
      setSubmitting(false);
    }
  };

  // 测试弹窗内临时连接配置
  const handleTempTest = async () => {
    try {
      await form.validateFields(["engine", "database"]);
    } catch {
      message.warning("请先填写引擎与数据库名");
      return;
    }
    const values = form.getFieldsValue(true) as DatasourceFormValues;
    const payload: TestConnection = {
      engine: values.engine,
      database: values.database,
      host: values.host,
      port: values.port ?? null,
      username: values.username,
      password: values.password,
    };
    setTempTesting(true);
    try {
      const result = await testTempConnection(payload);
      if (result.ok) message.success(result.detail || "连接成功");
      else message.error(result.detail || "连接失败");
    } catch (err) {
      message.error(errMsg(err, "测试失败"));
    } finally {
      setTempTesting(false);
    }
  };

  const columns: ColumnsType<DataSource> = [
    { title: "名称", dataIndex: "name" },
    {
      title: "引擎",
      dataIndex: "engine",
      width: 120,
      render: (e: EngineType) => (
        <Tag color={engineColor[e]}>{engineLabel[e]}</Tag>
      ),
    },
    {
      title: "地址",
      key: "addr",
      width: 180,
      render: (_, r) =>
        r.engine === "sqlite" ? (
          <Text type="secondary">—</Text>
        ) : (
          <Text>
            {r.host}
            {r.port ? `:${r.port}` : ""}
          </Text>
        ),
    },
    { title: "数据库", dataIndex: "database" },
    {
      title: "分组",
      dataIndex: "group",
      width: 120,
      render: (v: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: "标签",
      dataIndex: "tags",
      render: (tags: string[]) =>
        tags && tags.length ? (
          <Space wrap size={[4, 4]}>
            {tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      render: (active: boolean, record) => (
        <Switch
          checked={active}
          disabled={!admin}
          onChange={() => handleToggleActive(record)}
        />
      ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
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
            icon={<ThunderboltOutlined />}
            loading={testingId === record.id}
            onClick={() => handleTestSaved(record)}
          >
            测试
          </Button>
          {admin && (
            <>
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => openEdit(record)}
              >
                编辑
              </Button>
              <Popconfirm
                title="确认删除该数据源？"
                okText="删除"
                cancelText="取消"
                onConfirm={() => handleDelete(record.id)}
              >
                <Button size="small" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        {admin && (
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增数据源
          </Button>
        )}
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: "暂无数据源" }}
      />
      <Modal
        title={editing ? `编辑数据源 - ${editing.name}` : "新增数据源"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        footer={[
          <Button
            key="test"
            icon={<ThunderboltOutlined />}
            loading={tempTesting}
            onClick={handleTempTest}
          >
            测试连接
          </Button>,
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
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="请输入数据源名称" />
          </Form.Item>
          <Form.Item
            name="engine"
            label="引擎"
            rules={[{ required: true, message: "请选择引擎" }]}
          >
            <Select options={engineOptions} placeholder="请选择引擎" />
          </Form.Item>
          {engineValue !== "sqlite" && (
            <>
              <Form.Item name="host" label="主机">
                <Input placeholder="如 127.0.0.1" />
              </Form.Item>
              <Form.Item name="port" label="端口">
                <InputNumber
                  style={{ width: "100%" }}
                  placeholder="如 3306"
                  min={1}
                  max={65535}
                />
              </Form.Item>
              <Form.Item name="username" label="用户名">
                <Input placeholder="请输入用户名" />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                extra={editing ? "留空则保持原密码" : undefined}
              >
                <Input.Password placeholder="请输入密码" />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="database"
            label="数据库"
            rules={[{ required: true, message: "请输入数据库名" }]}
          >
            <Input placeholder="请输入数据库名或文件路径" />
          </Form.Item>
          <Form.Item name="group" label="分组">
            <Input placeholder="default" />
          </Form.Item>
          <Form.Item name="tags" label="标签">
            <Select mode="tags" placeholder="输入后回车添加" tokenSeparators={[",", " "]} />
          </Form.Item>
          {editing && (
            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </>
  );
};

export default Datasources;
