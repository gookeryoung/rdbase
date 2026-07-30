import { useState } from "react";
import { Card, Form, Input, Button, message, Typography, Descriptions, Space } from "antd";
import { useAuthStore } from "@/store/auth";
import { changePassword } from "@/api/auth";
import { Role } from "@/types";

const { Title } = Typography;

// 角色中文标签
const roleLabel: Record<Role, string> = {
  [Role.ADMIN]: "管理员",
  [Role.DESIGNER]: "设计者",
  [Role.VIEWER]: "查看者",
};

interface ChangePasswordForm {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

// 个人中心：展示当前用户信息并提供修改密码功能
const Profile = () => {
  const user = useAuthStore((state) => state.user);
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: ChangePasswordForm) => {
    if (values.new_password !== values.confirm_password) {
      message.error("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      await changePassword(values.old_password, values.new_password);
      message.success("密码修改成功");
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card>
        <Title level={5}>个人信息</Title>
        <Descriptions column={1}>
          <Descriptions.Item label="用户名">{user?.username ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="邮箱">{user?.email || "—"}</Descriptions.Item>
          <Descriptions.Item label="角色">
            {user ? roleLabel[user.role] : "—"}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card style={{ maxWidth: 480 }}>
        <Title level={5}>修改密码</Title>
        <Form<ChangePasswordForm> name="change-password" onFinish={onFinish} layout="vertical">
          <Form.Item
            name="old_password"
            label="旧密码"
            rules={[{ required: true, message: "请输入旧密码" }]}
          >
            <Input.Password placeholder="请输入旧密码" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[{ required: true, message: "请输入新密码" }]}
          >
            <Input.Password placeholder="请输入新密码" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            rules={[{ required: true, message: "请再次输入新密码" }]}
          >
            <Input.Password placeholder="请再次输入新密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting}>
              确认修改
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </Space>
  );
};

export default Profile;
