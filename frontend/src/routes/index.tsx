import type { RouteObject } from "react-router-dom";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Users from "@/pages/Users";
import Profile from "@/pages/Profile";
import Datasources from "@/pages/Datasources";
import Designer from "@/pages/Designer";
import Manager from "@/pages/Manager";
import SqlConsole from "@/pages/SqlConsole";
import AuditLogs from "@/pages/AuditLogs";
import Settings from "@/pages/Settings";
import Sync from "@/pages/Sync";
import Ingest from "@/pages/Ingest";
import SystemStatus from "@/pages/SystemStatus";
import MainLayout from "@/layouts/MainLayout";
import ProtectedRoute from "@/components/ProtectedRoute";
import RoleRoute from "@/components/RoleRoute";
import { Role } from "@/types";

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
            children: [{ index: true, element: <Designer /> }],
          },
          { path: "manager", element: <Manager /> },
          { path: "sql-console", element: <SqlConsole /> },
          {
            path: "users",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <Users /> }],
          },
          {
            path: "audit",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <AuditLogs /> }],
          },
          {
            path: "settings",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <Settings /> }],
          },
          {
            path: "sync",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <Sync /> }],
          },
          {
            path: "ingest",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <Ingest /> }],
          },
          {
            path: "system-status",
            element: <RoleRoute allowedRoles={[Role.ADMIN]} />,
            children: [{ index: true, element: <SystemStatus /> }],
          },
          { path: "profile", element: <Profile /> },
        ],
      },
    ],
  },
];
