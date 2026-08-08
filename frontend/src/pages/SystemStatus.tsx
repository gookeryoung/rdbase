import { useCallback, useEffect, useState } from "react";
import {
  Card,
  Table,
  Tag,
  Button,
  Space,
  message,
  Typography,
  Spin,
  Alert,
} from "antd";
import {
  ReloadOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { getHealth, getPoolStats } from "@/api/system";
import type {
  ComponentStatus,
  HealthStatus,
  HealthSummary,
  PoolStat,
  PoolStatsList,
} from "@/types";

const { Text } = Typography;

// 组件中文名
const componentLabel: Record<string, string> = {
  db: "数据库",
  disk: "磁盘空间",
  redis: "Redis",
  pools: "连接池",
};

// 状态中文标签与颜色
const statusMeta: Record<HealthStatus, { label: string; color: string }> = {
  healthy: { label: "健康", color: "green" },
  degraded: { label: "降级", color: "orange" },
  unhealthy: { label: "不可用", color: "red" },
};

const SystemStatusPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [poolStats, setPoolStats] = useState<PoolStatsList | null>(null);
  const [error, setError] = useState<string>("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [h, p] = await Promise.all([getHealth(), getPoolStats()]);
      setHealth(h);
      setPoolStats(p);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "加载系统状态失败";
      setError(msg);
      void message.error(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const poolColumns: ColumnsType<PoolStat> = [
    { title: "ID", dataIndex: "datasource_id", width: 80 },
    {
      title: "数据源",
      dataIndex: "datasource_name",
      render: (name: string | null, record: PoolStat) =>
        name ?? `#${record.datasource_id}`,
    },
    { title: "池大小", dataIndex: "pool_size", width: 90 },
    { title: "空闲", dataIndex: "checked_in", width: 80 },
    { title: "已借出", dataIndex: "checked_out", width: 90 },
    { title: "溢出", dataIndex: "overflow", width: 80 },
    {
      title: "泄露告警",
      dataIndex: "leak_alert",
      width: 110,
      render: (alert: boolean) =>
        alert ? <Tag color="red">疑似泄露</Tag> : <Tag color="green">正常</Tag>,
    },
    { title: "详情", dataIndex: "leak_detail", ellipsis: true },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} align="center">
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void fetchAll()}
          loading={loading}
        >
          刷新
        </Button>
        {health && (
          <>
            <Text type="secondary">整体状态：</Text>
            <Tag
              color={statusMeta[health.status].color}
              style={{ fontSize: 14, padding: "2px 12px" }}
            >
              {statusMeta[health.status].label}
            </Tag>
          </>
        )}
      </Space>

      {error && (
        <Alert
          type="error"
          message="加载失败"
          description={error}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      <Spin spinning={loading}>
        <Card title="组件健康检查" style={{ marginBottom: 16 }}>
          {health?.components.map((c) => (
            <ComponentRow key={c.name} component={c} />
          ))}
          {!health && !loading && (
            <Text type="secondary">暂无数据</Text>
          )}
        </Card>

        <Card title="数据源连接池">
          <Table<PoolStat>
            columns={poolColumns}
            dataSource={poolStats?.items ?? []}
            rowKey="datasource_id"
            pagination={false}
            size="small"
            locale={{ emptyText: "无活跃数据源引擎" }}
          />
        </Card>
      </Spin>
    </div>
  );
};

// 组件行：名称 + 状态 + 延迟 + 详情
const ComponentRow: React.FC<{ component: ComponentStatus }> = ({ component }) => {
  const meta = statusMeta[component.status];
  const icon =
    component.status === "healthy" ? (
      <CheckCircleOutlined style={{ color: meta.color }} />
    ) : component.status === "degraded" ? (
      <WarningOutlined style={{ color: meta.color }} />
    ) : (
      <CloseCircleOutlined style={{ color: meta.color }} />
    );
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        padding: "8px 0",
        borderBottom: "1px solid #f0f0f0",
      }}
    >
      <Space style={{ width: 200 }}>
        {icon}
        <Text strong>{componentLabel[component.name] ?? component.name}</Text>
      </Space>
      <Tag color={meta.color} style={{ width: 80, textAlign: "center" }}>
        {meta.label}
      </Tag>
      <Text type="secondary" style={{ width: 100 }}>
        {component.latency_ms} ms
      </Text>
      <Text style={{ flex: 1 }}>{component.detail}</Text>
    </div>
  );
};

export default SystemStatusPage;
