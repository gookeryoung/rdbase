import { useState } from "react";
import { Card, Form, Input, Button, Typography, message } from "antd";
import { UserOutlined, LockOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { login as loginApi } from "@/api/auth";
import { useAuthStore } from "@/store/auth";
import type { LoginRequest } from "@/types";

const { Title } = Typography;

// 登录页：居中卡片表单，提交时调用 /auth/login 接口
const Login = () => {
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const storeLogin = useAuthStore((state) => state.login);

  const onFinish = async (values: LoginRequest) => {
    setSubmitting(true);
    try {
      const { access, user } = await loginApi(values);
      storeLogin(access, user);
      message.success("登录成功");
      navigate("/", { replace: true });
    } catch (err) {
      // axios 错误统一由拦截器处理 401；其它错误在此提示
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "登录失败，请稍后重试");
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
