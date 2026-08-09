import { useState, type ReactNode } from "react";
import { Layout, Menu, Space, Typography, Dropdown, Avatar } from "antd";
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
  CloudDownloadOutlined,
  MonitorOutlined,
  AppstoreOutlined,
  BellOutlined,
  KeyOutlined,
  DownOutlined,
  ControlOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { useAuthStore } from "@/store/auth";
import { logout as logoutApi } from "@/api/auth";
import { hasRole } from "@/utils/permission";
import { Role, type User } from "@/types";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

interface MenuItem {
  key: string;
  icon?: ReactNode;
  label: string;
  /** 角色限制：为空表示所有角色可见 */
  roles?: Role[];
  /** 子菜单项（存在时该项作为 SubMenu 分组标题） */
  children?: MenuItem[];
}

// 侧边栏菜单项配置（按业务域分组）：
// - 仪表盘独立置顶
// - 「数据」：数据源/数据集/数据库设计/数据库管理/SQL 控制台
// - 「调度」：数据同步/数据爬取（admin）
// - 「系统管理」：用户/审计/系统设置/系统状态/Webhook/Token（admin）
// 个人中心移至顶部用户下拉菜单（与登出合并）
const menuItems: MenuItem[] = [
  { key: "/", icon: <DashboardOutlined />, label: "仪表盘" },
  {
    key: "group-data",
    icon: <DatabaseOutlined />,
    label: "数据",
    children: [
      { key: "/datasources", icon: <DatabaseOutlined />, label: "数据源" },
      {
        key: "/datasets",
        icon: <AppstoreOutlined />,
        label: "数据集",
        roles: [Role.ADMIN],
      },
      {
        key: "/designer",
        icon: <ApartmentOutlined />,
        label: "数据库设计",
        roles: [Role.ADMIN, Role.DESIGNER],
      },
      { key: "/manager", icon: <TableOutlined />, label: "数据库管理" },
      { key: "/sql-console", icon: <CodeOutlined />, label: "SQL 控制台" },
    ],
  },
  {
    key: "group-schedule",
    icon: <SyncOutlined />,
    label: "调度",
    roles: [Role.ADMIN],
    children: [
      { key: "/sync", icon: <SyncOutlined />, label: "数据同步", roles: [Role.ADMIN] },
      { key: "/ingest", icon: <CloudDownloadOutlined />, label: "数据爬取", roles: [Role.ADMIN] },
    ],
  },
  {
    key: "group-system",
    icon: <ControlOutlined />,
    label: "系统管理",
    roles: [Role.ADMIN],
    children: [
      { key: "/users", icon: <TeamOutlined />, label: "用户管理", roles: [Role.ADMIN] },
      { key: "/audit", icon: <FileSearchOutlined />, label: "审计日志", roles: [Role.ADMIN] },
      { key: "/settings", icon: <SettingOutlined />, label: "系统设置", roles: [Role.ADMIN] },
      { key: "/system-status", icon: <MonitorOutlined />, label: "系统状态", roles: [Role.ADMIN] },
      { key: "/webhooks", icon: <BellOutlined />, label: "Webhook 订阅", roles: [Role.ADMIN] },
      { key: "/tokens", icon: <KeyOutlined />, label: "API Token", roles: [Role.ADMIN] },
    ],
  },
];

// 判断菜单项对当前用户是否可见（含子菜单时需至少一个子项可见）
const isItemVisible = (item: MenuItem, user: User | null): boolean => {
  if (item.roles && !hasRole(user, ...item.roles)) return false;
  if (item.children) {
    return item.children.some((child) => isItemVisible(child, user));
  }
  return true;
};

// 递归过滤后的菜单节点：
// - 父节点（分组）含 children 数组
// - 叶子节点不含 children 字段（满足 antd Menu SubMenuType 类型约束）
interface MenuParent {
  key: string;
  icon?: ReactNode;
  label: string;
  children: MenuNode[];
}
interface MenuLeaf {
  key: string;
  icon?: ReactNode;
  label: string;
}
type MenuNode = MenuParent | MenuLeaf;

// 递归过滤菜单项，返回 antd Menu 接受的结构（含 children 转 SubMenu）
const filterMenuItems = (user: User | null): MenuNode[] =>
  menuItems
    .filter((item) => isItemVisible(item, user))
    .map((item): MenuNode => {
      if (item.children) {
        return {
          key: item.key,
          icon: item.icon,
          label: item.label,
          children: item.children
            .filter((child) => isItemVisible(child, user))
            .map((child): MenuLeaf => ({
              key: child.key,
              icon: child.icon,
              label: child.label,
            })),
        };
      }
      return { key: item.key, icon: item.icon, label: item.label };
    });

// 计算当前路径所属的 SubMenu key（用于默认展开）
const computeOpenKeys = (pathname: string): string[] => {
  const openKeys: string[] = [];
  for (const item of menuItems) {
    if (item.children?.some((child) => child.key === pathname)) {
      openKeys.push(item.key);
    }
  }
  return openKeys;
};

// 主布局：可折叠侧边栏（按业务域分组）+ 顶部用户下拉（个人中心/登出）+ 内容区 Outlet
const MainLayout = () => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  const visibleMenu = filterMenuItems(user);
  // 进入页面时默认展开当前路径所属分组
  const [openKeys, setOpenKeys] = useState<string[]>(() => computeOpenKeys(location.pathname));

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

  // 顶部用户下拉菜单项
  const userMenuItems: MenuProps["items"] = [
    {
      key: "profile",
      icon: <UserOutlined />,
      label: "个人中心",
      onClick: () => navigate("/profile"),
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "登出",
      onClick: handleLogout,
    },
  ];

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
          openKeys={collapsed ? [] : openKeys}
          onOpenChange={(keys) => setOpenKeys(keys as string[])}
          items={visibleMenu as MenuProps["items"]}
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
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Space style={{ cursor: "pointer" }}>
              <Avatar size="small" icon={<UserOutlined />} />
              <Text>{user?.username ?? "未登录"}</Text>
              <DownOutlined style={{ fontSize: 12 }} />
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: "#fff", borderRadius: 8 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
