import { Navigate, Outlet } from "react-router-dom";
import { Result } from "antd";
import { useAuthStore } from "@/store/auth";
import { hasRole } from "@/utils/permission";
import type { Role } from "@/types";

interface RoleRouteProps {
  /** 允许通过的角色列表；为空则任意已登录用户均可 */
  allowedRoles?: Role[];
}

// 角色路由守卫：未登录跳 /login；已登录但角色不足显示 403 页面
const RoleRoute = ({ allowedRoles = [] }: RoleRouteProps) => {
  const token = useAuthStore((state) => state.token);
  const user = useAuthStore((state) => state.user);

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // 未指定角色限制时，仅校验登录状态
  if (allowedRoles.length === 0) {
    return <Outlet />;
  }

  if (!hasRole(user, ...allowedRoles)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您无权访问该页面。"
      />
    );
  }

  return <Outlet />;
};

export default RoleRoute;
