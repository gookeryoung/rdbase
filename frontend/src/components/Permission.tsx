import type { ReactNode } from "react";
import { useAuthStore } from "@/store/auth";
import { hasRole } from "@/utils/permission";
import type { Role } from "@/types";

interface PermissionProps {
  /** 允许通过的角色列表 */
  allowedRoles: Role[];
  /** 通过校验时渲染的内容 */
  children: ReactNode;
  /** 未通过校验时渲染的兜底内容（默认不渲染） */
  fallback?: ReactNode;
}

// 按钮级权限：用户角色不在允许列表中则渲染 fallback
const Permission = ({ allowedRoles, children, fallback = null }: PermissionProps) => {
  const user = useAuthStore((state) => state.user);
  if (!hasRole(user, ...allowedRoles)) {
    return <>{fallback}</>;
  }
  return <>{children}</>;
};

export default Permission;
