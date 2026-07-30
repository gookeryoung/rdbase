import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite 配置：启用 React 插件，配置路径别名与开发服务器代理
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 后端 API 代理到本地 8000 端口
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // 健康检查接口代理
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
