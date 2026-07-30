import { useState } from "react";
import { Card, Form, Input, Button, Typography, message } from "antd";
import { UserOutlined, LockOutlined } from "@ant-design/icons";
import type { LoginRequest } from "@/types";

const { Title } = Typography;

// 登录页：居中卡片表单，提交时调用登录接口（P1 阶段实现真实调用与跳转）
const Login = () => {
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: LoginRequest) => {
    // P1 实现：调用 /api/v1/auth/login 获取 token 与用户信息，写入 auth store 后跳转主页
    setSubmitting(true);
    try {
      console.log("登录表单提交：", values);
      message.info("登录功能将在 P1 阶段实现");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        background: "#f0f2f5",
      }}
    >
      <Card style={{ width: 400 }}>
        <Title level={3} style={{ textAlign: "center", marginBottom: 24 }}>
          rdbase 数据库管理平台
        </Title>
        <Form name="login" onFinish={onFinish} size="large" initialValues={{ remember: true }}>
          <Form.Item name="username" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Login;
