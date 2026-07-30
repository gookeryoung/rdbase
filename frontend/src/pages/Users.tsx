import { useEffect, useState, type ReactNode } from "react";
import { Table, Tag, Switch, Button, Modal, Input, Select, Space, message, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { KeyOutlined } from "@ant-design/icons";
import { listUsers, toggleUserActive, resetUserPassword, updateUserRole, type UserItem } from "@/api/users";
import { Role } from "@/types";

const { Text } = Typography;

// 角色标签颜色映射
const roleColor: Record<Role, string> = {
  [Role.ADMIN]: "red",
  [Role.DESIGNER]: "blue",
  [Role.VIEWER]: "default",
};

// 角色中文标签
const roleLabel: Record<Role, string> = {
  [Role.ADMIN]: "管理员",
  [Role.DESIGNER]: "设计者",
  [Role.VIEWER]: "查看者",
};

// 用户管理页：管理员独占，提供列表、启用禁用、重置密码、改角色
const Users = () => {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [resetTarget, setResetTarget] = useState<UserItem | null>(null);
  const [newPassword, setNewPassword] = useState("");

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "加载用户列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadUsers();
  }, []);

  const handleToggleActive = async (record: UserItem) => {
    try {
      const updated = await toggleUserActive(record.id);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      message.success(updated.is_active ? "已启用" : "已禁用");
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "操作失败");
    }
  };

  const handleResetPassword = async () => {
    if (!resetTarget || !newPassword) return;
    try {
      await resetUserPassword(resetTarget.id, newPassword);
      message.success(`已重置 ${resetTarget.username} 的密码`);
      setResetTarget(null);
      setNewPassword("");
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "重置失败");
    }
  };

  const handleRoleChange = async (record: UserItem, role: Role) => {
    try {
      const updated = await updateUserRole(record.id, role);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      message.success("角色已更新");
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "修改角色失败");
    }
  };

  const columns: ColumnsType<UserItem> = [
    { title: "ID", dataIndex: "id", width: 60 },
    { title: "用户名", dataIndex: "username" },
    { title: "邮箱", dataIndex: "email", render: (v: string) => v || "—" },
    {
      title: "角色",
      dataIndex: "role",
      width: 160,
      render: (role: Role, record) => (
        <Space>
          <Tag color={roleColor[role]}>{roleLabel[role]}</Tag>
          <Select
            size="small"
            value={role}
            onChange={(v: Role) => handleRoleChange(record, v)}
            options={Object.values(Role).map((r) => ({ value: r, label: roleLabel[r] }))}
            style={{ width: 110 }}
          />
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (active: boolean, record) => (
        <Switch checked={active} onChange={() => handleToggleActive(record)} />
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 120,
      render: (_, record) => (
        <Button
          size="small"
          icon={<KeyOutlined />}
          onClick={() => setResetTarget(record)}
        >
          重置密码
        </Button>
      ),
    },
  ];

  const renderEmpty = (): ReactNode => <Text type="secondary">暂无用户</Text>;

  return (
    <>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={users}
        loading={loading}
        pagination={false}
        locale={{ emptyText: renderEmpty() }}
      />
      <Modal
        title={`重置密码 - ${resetTarget?.username ?? ""}`}
        open={resetTarget !== null}
        onOk={handleResetPassword}
        onCancel={() => {
          setResetTarget(null);
          setNewPassword("");
        }}
        okButtonProps={{ disabled: !newPassword }}
      >
        <Input.Password
          placeholder="请输入新密码"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          onPressEnter={handleResetPassword}
        />
      </Modal>
    </>
  );
};

export default Users;
