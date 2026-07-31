import { useState, type ReactNode } from "react";
import { Layout, Menu, Button, Space, Typography } from "antd";
import {
  DashboardOutlined,
  DatabaseOutlined,
  ApartmentOutlined,
  TableOutlined,
  CodeOutlined,
  LogoutOutlined,
  UserOutlined,
  TeamOutlined,
  FileSearchOutlined,
  SettingOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { useAuthStore } from "@/store/auth";
import { logout as logoutApi } from "@/api/auth";
import { hasRole } from "@/utils/permission";
import { Role, type User } from "@/types";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

interface MenuItem {
  key: string;
  icon: ReactNode;
  label: string;
  /** 角色限制：为空表示所有角色可见 */
  roles?: Role[];
}

// 侧边栏菜单项配置（数据库设计仅 designer/admin 可见；用户管理仅 admin 可见）
const menuItems: MenuItem[] = [
  { key: "/", icon: <DashboardOutlined />, label: "仪表盘" },
  { key: "/datasources", icon: <DatabaseOutlined />, label: "数据源" },
  {
    key: "/designer",
    icon: <ApartmentOutlined />,
    label: "数据库设计",
    roles: [Role.ADMIN, Role.DESIGNER],
  },
  { key: "/manager", icon: <TableOutlined />, label: "数据库管理" },
  { key: "/sql-console", icon: <CodeOutlined />, label: "SQL 控制台" },
  {
    key: "/users",
    icon: <TeamOutlined />,
    label: "用户管理",
    roles: [Role.ADMIN],
  },
  {
    key: "/audit",
    icon: <FileSearchOutlined />,
    label: "审计日志",
    roles: [Role.ADMIN],
  },
  {
    key: "/settings",
    icon: <SettingOutlined />,
    label: "系统设置",
    roles: [Role.ADMIN],
  },
  {
    key: "/sync",
    icon: <SyncOutlined />,
    label: "数据同步",
    roles: [Role.ADMIN],
  },
  { key: "/profile", icon: <UserOutlined />, label: "个人中心" },
];

// 按用户角色过滤菜单项，返回 antd Menu 接受的结构
const filterMenuItems = (user: User | null): { key: string; icon: ReactNode; label: string }[] =>
  menuItems
    .filter((item) => !item.roles || hasRole(user, ...item.roles))
    .map(({ key, icon, label }) => ({ key, icon, label }));

// 主布局：可折叠侧边栏 + 顶部用户信息 + 内容区 Outlet
const MainLayout = () => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  const visibleMenu = filterMenuItems(user);

  const handleLogout = async () => {
    // 通知后端清除 refresh cookie，并清除本地认证状态
    try {
      await logoutApi();
    } catch {
      // 后端登出失败不阻塞本地清理
    }
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div
          style={{
            height: 32,
            margin: 16,
            color: "#fff",
            textAlign: "center",
            lineHeight: "32px",
            fontSize: 18,
          }}
        >
          rdbase
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={visibleMenu}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: "0 16px",
            background: "#fff",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
          }}
        >
          <Space>
            <UserOutlined />
            <Text>{user?.username ?? "未登录"}</Text>
            <Button type="link" icon={<LogoutOutlined />} onClick={handleLogout}>
              登出
            </Button>
          </Space>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: "#fff", borderRadius: 8 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
