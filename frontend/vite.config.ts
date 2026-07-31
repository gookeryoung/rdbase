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
  build: {
    // 代码分割：将第三方库拆分为独立 chunk，优化首屏加载
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router-dom"],
          "antd-vendor": ["antd", "@ant-design/icons"],
          "monaco-vendor": ["@monaco-editor/react"],
          "reactflow-vendor": ["reactflow"],
        },
      },
    },
    // chunk 大小警告阈值（KB）
    chunkSizeWarningLimit: 1000,
  },
});
