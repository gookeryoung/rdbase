import axios, { AxiosError } from "axios";

// 创建 axios 实例，统一配置 baseURL 与超时时间
const client = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
});

// 请求拦截器：从 localStorage 读取 token 并附加到请求头
client.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("rdbase_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：401 时清除凭证并跳转到登录页
client.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("rdbase_token");
      localStorage.removeItem("rdbase_user");
      // 避免在登录页重复跳转
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default client;
