import type { RouteObject } from "react-router-dom";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import MainLayout from "@/layouts/MainLayout";
import ProtectedRoute from "@/components/ProtectedRoute";
import { Typography } from "antd";

const { Text } = Typography;

// 占位页面组件：未实现模块统一占位
const Placeholder = ({ title }: { title: string }) => (
  <div style={{ padding: 24, textAlign: "center" }}>
    <Text type="secondary">{title}（占位页面，待后续阶段实现）</Text>
  </div>
);

// 路由配置：未登录访问受保护路由时由 ProtectedRoute 重定向到 /login
export const routes: RouteObject[] = [
  {
    path: "/login",
    element: <Login />,
  },
  {
    path: "/",
    element: <ProtectedRoute />,
    children: [
      {
        element: <MainLayout />,
        children: [
          { index: true, element: <Dashboard /> },
          { path: "datasources", element: <Placeholder title="数据源管理" /> },
          { path: "designer", element: <Placeholder title="数据库设计" /> },
          { path: "manager", element: <Placeholder title="数据库管理" /> },
        ],
      },
    ],
  },
];
