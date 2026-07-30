import { useRoutes } from "react-router-dom";
import { routes } from "@/routes";

// 应用根组件：根据路由配置渲染对应页面
const App = () => {
  const element = useRoutes(routes);
  return element;
};

export default App;
