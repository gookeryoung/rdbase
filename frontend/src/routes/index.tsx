import type { RouteObject } from "react-router-dom";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Users from "@/pages/Users";
import Profile from "@/pages/Profile";
import Datasources from "@/pages/Datasources";
import MainLayout from "@/layouts/MainLayout";
import ProtectedRoute from "@/components/ProtectedRoute";
import RoleRoute from "@/components/RoleRoute";
import { Typography } from "antd";
import { Role } from "@/types";

const { Text } = Typography;

// 占位页面组件：未实现模块统一占位
const Placeholder = ({ title }: { title: string }) => (
  <div style={{ padding: 24, textAlign: "center" }}>
    <Text type="secondary">{title}（占位页面，待后续阶段实现）</Text>
  </div>
);

// 路由配置：
// - ProtectedRoute：登录守卫
// - RoleRoute：角色守卫（未登录跳 /login，已登录但角色不足显示 403）
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
          { path: "datasources", element: <Datasources /> },
          {
            path: "designer",
            element: <RoleRoute allowedRoles={[Role.ADMIN, Role.DESIGNER]} />,
            children: [{ index: true, element: <Placeholder title="数据库设计" /> }],
          },
          { path: "manager", element: <Placeholder title="数据库管理" /> },
          {
            path: "users",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <Users /> }],
          },
          { path: "profile", element: <Profile /> },
        ],
      },
    ],
  },
];
