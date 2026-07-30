import { Role, type User } from "@/types";

// 判断用户是否拥有指定角色之一
export const hasRole = (user: User | null, ...roles: Role[]): boolean => {
  if (!user) return false;
  return roles.includes(user.role);
};

// 是否管理员（拥有全部权限）
export const isAdmin = (user: User | null): boolean => hasRole(user, Role.ADMIN);

// 是否设计者或管理员（viewer 不可访问）
export const isDesignerOrAdmin = (user: User | null): boolean =>
  hasRole(user, Role.DESIGNER, Role.ADMIN);
