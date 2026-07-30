import { Navigate, Outlet } from "react-router-dom";
import { useAuthStore } from "@/store/auth";

// 路由守卫：未登录（无 token）时重定向到 /login
const ProtectedRoute = () => {
  const token = useAuthStore((state) => state.token);
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
};

export default ProtectedRoute;
