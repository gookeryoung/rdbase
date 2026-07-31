import { useCallback, useEffect, useState } from "react";
import {
  Table,
  Button,
  Input,
  Select,
  Space,
  message,
  Modal,
  Tag,
  Descriptions,
  Spin,
  Alert,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  EditOutlined,
  ReloadOutlined,
  SwapOutlined,
  KeyOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import {
  listSettings,
  updateSetting,
  listPresets,
  initSettings,
  rotateKey,
} from "@/api/settings";
import type {
  SystemSetting,
  SystemSettingUpdate,
  ValueType,
} from "@/types";

const { Text } = Typography;

// 值类型中文标签
const valueTypeLabel: Record<ValueType, string> = {
  str: "字符串",
  int: "整数",
  bool: "布尔",
  json: "JSON",
};

// 值类型标签颜色
const valueTypeColor: Record<ValueType, string> = {
  str: "blue",
  int: "green",
  bool: "purple",
  json: "orange",
};

// 按 key 前缀分组的显示名
const groupLabels: Record<string, string> = {
  session: "会话超时",
  password: "密码策略",
  encryption: "加密密钥",
};

function getGroup(key: string): string {
  const prefix = key.split(".")[0];
  return groupLabels[prefix] ?? "其他";
}

const SettingsPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [settings, setSettings] = useState<SystemSetting[]>([]);
  const [presets, setPresets] = useState<SystemSetting[]>([]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [rotateModalOpen, setRotateModalOpen] = useState(false);
  const [rotateNewKey, setRotateNewKey] = useState("");
  const [rotating, setRotating] = useState(false);

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSettings();
      setSettings(data.items);
    } catch {
      message.error("加载系统设置失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchPresets = useCallback(async () => {
    try {
      const data = await listPresets();
      setPresets(data);
    } catch {
      // 预置接口可能不存在（老版本），静默忽略
    }
  }, []);

  useEffect(() => {
    fetchSettings();
    fetchPresets();
  }, [fetchSettings, fetchPresets]);

  const handleEdit = (record: SystemSetting) => {
    setEditingKey(record.key);
    setEditValue(record.value);
    setEditDesc(record.description);
  };

  const handleSave = async () => {
    if (!editingKey) return;
    try {
      const payload: SystemSettingUpdate = {
        value: editValue,
        description: editDesc,
      };
      await updateSetting(editingKey, payload);
      message.success("设置已更新");
      setEditingKey(null);
      fetchSettings();
    } catch {
      message.error("更新失败");
    }
  };

  const handleInit = async () => {
    try {
      const result = await initSettings();
      message.success(result.detail);
      fetchSettings();
    } catch {
      message.error("初始化失败");
    }
  };

  const handleRotateKey = async () => {
    setRotating(true);
    try {
      const result = await rotateKey({
        confirm: true,
        new_key: rotateNewKey || undefined,
      });
      if (result.success) {
        message.success(result.message);
        setRotateModalOpen(false);
        setRotateNewKey("");
      } else {
        message.error(result.message);
      }
    } catch (err: unknown) {
      message.error("密钥轮换失败");
    } finally {
      setRotating(false);
    }
  };

  // 按分组排序
  const sortedSettings = [...settings].sort((a, b) => a.key.localeCompare(b.key));

  const columns: ColumnsType<SystemSetting> = [
    {
      title: "分组",
      dataIndex: "key",
      key: "group",
      width: 100,
      render: (key: string) => <Tag>{getGroup(key)}</Tag>,
      sorter: (a, b) => getGroup(a.key).localeCompare(getGroup(b.key)),
      defaultSortOrder: "ascend",
    },
    {
      title: "设置键",
      dataIndex: "key",
      key: "key",
      width: 280,
      render: (key: string) => <Text code>{key}</Text>,
    },
    {
      title: "值",
      dataIndex: "value",
      key: "value",
      width: 200,
      render: (value: string, record: SystemSetting) => {
        if (record.value_type === "bool") {
          return value === "true" || value === "True" ? (
            <Tag color="green">true</Tag>
          ) : (
            <Tag color="red">false</Tag>
          );
        }
        if (record.value_type === "json" && value) {
          return (
            <Text code ellipsis style={{ maxWidth: 200 }}>
              {value.length > 50 ? value.slice(0, 50) + "..." : value}
            </Text>
          );
        }
        return <Text>{value}</Text>;
      },
    },
    {
      title: "类型",
      dataIndex: "value_type",
      key: "value_type",
      width: 100,
      render: (vt: ValueType) => (
        <Tag color={valueTypeColor[vt]}>{valueTypeLabel[vt]}</Tag>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 170,
      render: (t: string) => t?.replace("T", " ").slice(0, 19) || "-",
    },
    {
      title: "操作",
      key: "action",
      width: 80,
      render: (_: unknown, record: SystemSetting) => (
        <Button
          type="link"
          size="small"
          icon={<EditOutlined />}
          onClick={() => handleEdit(record)}
        >
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Space>
          <SettingOutlined style={{ fontSize: 20 }} />
          <Text strong style={{ fontSize: 18 }}>
            系统设置
          </Text>
        </Space>

        <Alert
          type="info"
          showIcon
          message="配置说明"
          description={
            <Space direction="vertical" size={4}>
              <span>会话超时：控制 JWT access/refresh token 有效期</span>
              <span>密码策略：控制用户密码复杂度要求</span>
              <span>加密密钥轮换：重新加密所有数据源的连接密码</span>
            </Space>
          }
        />

        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchSettings}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            icon={<KeyOutlined />}
            onClick={() => setRotateModalOpen(true)}
            danger
          >
            加密密钥轮换
          </Button>
          {presets.length > 0 && (
            <Button onClick={handleInit}>初始化预置项</Button>
          )}
        </Space>

        <Spin spinning={loading}>
          <Table
            rowKey="key"
            columns={columns}
            dataSource={sortedSettings}
            pagination={false}
            size="middle"
            bordered
          />
        </Spin>
      </Space>

      {/* 编辑对话框 */}
      <Modal
        title={editingKey ? `编辑设置：${editingKey}` : "编辑设置"}
        open={editingKey !== null}
        onOk={handleSave}
        onCancel={() => setEditingKey(null)}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        {editingKey && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="设置键">
              <Text code>{editingKey}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="值">
              <Space direction="vertical" style={{ width: "100%" }}>
                {(() => {
                  const record = settings.find((s) => s.key === editingKey);
                  const vt = record?.value_type ?? "str";
                  if (vt === "bool") {
                    return (
                      <Select
                        value={editValue}
                        onChange={setEditValue}
                        style={{ width: 120 }}
                        options={[
                          { label: "true", value: "true" },
                          { label: "false", value: "false" },
                        ]}
                      />
                    );
                  }
                  if (vt === "json") {
                    return (
                      <Input.TextArea
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        rows={4}
                        placeholder='{"key": "value"}'
                      />
                    );
                  }
                  return (
                    <Input
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                    />
                  );
                })()}
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="描述">
              <Input
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                placeholder="可选"
              />
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      {/* 密钥轮换对话框 */}
      <Modal
        title={
          <Space>
            <SwapOutlined />
            <span>加密密钥轮换</span>
          </Space>
        }
        open={rotateModalOpen}
        onOk={handleRotateKey}
        onCancel={() => {
          setRotateModalOpen(false);
          setRotateNewKey("");
        }}
        okText="确认轮换"
        cancelText="取消"
        confirmLoading={rotating}
        okButtonProps={{ danger: true }}
        width={560}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="warning"
            showIcon
            message="危险操作"
            description="此操作将用新密钥重新加密所有数据源的连接密码。操作前请确保已备份。轮换后旧密钥将失效，已签发的 JWT token 不受影响。"
          />
          <div>
            <Text strong>新密钥（可选）：</Text>
            <Input.Password
              value={rotateNewKey}
              onChange={(e) => setRotateNewKey(e.target.value)}
              placeholder="留空将自动生成随机密钥"
              style={{ marginTop: 8 }}
            />
          </div>
          <Alert
            type="info"
            showIcon
            message="提示"
            description="密钥须为 Fernet 兼容的 urlsafe base64 字符串；留空时系统自动生成 256 位随机密钥。"
          />
        </Space>
      </Modal>
    </div>
  );
};

export default SettingsPage;
