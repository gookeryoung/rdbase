import client from "./client";
import type { HealthSummary, PoolStatsList } from "@/types";

// 获取详细健康检查（DB/磁盘/Redis/连接池）
export const getHealth = (): Promise<HealthSummary> =>
    client.get<HealthSummary>("/system/health").then((res) => res.data);

// 获取数据源连接池状态
export const getPoolStats = (): Promise<PoolStatsList> =>
    client.get<PoolStatsList>("/system/pool-stats").then((res) => res.data);
