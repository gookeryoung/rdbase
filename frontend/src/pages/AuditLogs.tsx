import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Table,
  Tag,
  Button,
  Input,
  Select,
  Space,
  message,
  Typography,
  Drawer,
  Descriptions,
  Tooltip,
  Spin,
} from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import { ExportOutlined, ReloadOutlined, EyeOutlined } from "@ant-design/icons";
import {
  listAuditLogs,
  retrieveAuditLog,
  exportAuditLogs,
} from "@/api/audit";
import type {
  AuditAction,
  AuditLog,
  AuditLogQuery,
  AuditSource,
  AuditStatus,
} from "@/types";

const { Text, Paragraph } = Typography;

// 操作类型中文标签
const actionLabel: Record<AuditAction, string> = {
  write: "写操作",
  login: "登录",
  logout: "登出",
  "datasource.create": "创建数据源",
  "datasource.update": "更新数据源",
  "datasource.delete": "删除数据源",
  "datasource.scan": "扫描数据源",
  "dataset.create": "创建数据集",
  "dataset.update": "更新数据集",
  "dataset.delete": "删除数据集",
  "dataset.write": "写入数据集",
  "draft.create": "创建草稿",
  "draft.update": "更新草稿",
  "draft.delete": "删除草稿",
  "draft.rollback": "回滚版本",
  "ddl.apply": "应用 DDL",
  "dml.insert": "新增行",
  "dml.update": "更新行",
  "dml.delete": "删除行",
  "dml.import": "导入数据",
  "sql.execute": "执行 SQL",
  "obj.alter": "编辑对象",
  "obj.drop": "删除对象",
  "backup.create": "创建备份",
  "backup.restore": "恢复备份",
  "audit.verify": "审计校验",
  "token.create": "创建 Token",
  "token.revoke": "吊销 Token",
  "token.rotate": "轮换 Token",
  "sync.trigger": "触发同步",
  "ingest.trigger": "触发爬取",
  "webhook.deliver": "Webhook 投递",
};

// 操作类型标签颜色（按类别分组）
const actionColor: Record<AuditAction, string> = {
  write: "default",
  login: "blue",
  logout: "blue",
  "datasource.create": "green",
  "datasource.update": "orange",
  "datasource.delete": "red",
  "datasource.scan": "cyan",
  "dataset.create": "green",
  "dataset.update": "orange",
  "dataset.delete": "red",
  "dataset.write": "cyan",
  "draft.create": "green",
  "draft.update": "orange",
  "draft.delete": "red",
  "draft.rollback": "purple",
  "ddl.apply": "magenta",
  "dml.insert": "green",
  "dml.update": "orange",
  "dml.delete": "red",
  "dml.import": "cyan",
  "sql.execute": "magenta",
  "obj.alter": "orange",
  "obj.drop": "red",
  "backup.create": "blue",
  "backup.restore": "purple",
  "audit.verify": "gold",
  "token.create": "green",
  "token.revoke": "red",
  "token.rotate": "orange",
  "sync.trigger": "blue",
  "ingest.trigger": "blue",
  "webhook.deliver": "geekblue",
};

// 来源中文标签
const sourceLabel: Record<AuditSource, string> = {
  middleware: "中间件",
  business: "业务层",
};

// 状态中文标签与颜色
const statusLabel: Record<AuditStatus, string> = {
  success: "成功",
  failure: "失败",
};
const statusColor: Record<AuditStatus, string> = {
  success: "green",
  failure: "red",
};

// 操作类型下拉选项
const actionOptions = (Object.keys(actionLabel) as AuditAction[]).map((a) => ({
  value: a,
  label: actionLabel[a],
}));
const sourceOptions = (Object.keys(sourceLabel) as AuditSource[]).map((s) => ({
  value: s,
  label: sourceLabel[s],
}));
const statusOptions = (Object.keys(statusLabel) as AuditStatus[]).map((s) => ({
  value: s,
  label: statusLabel[s],
}));

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// 格式化时间戳为本地可读形式
const formatTime = (v: string | null | undefined): string =>
  v ? new Date(v).toLocaleString("zh-CN") : "—";

// 审计日志页：管理员独占，提供分页列表、多条件筛选、详情抽屉、CSV 导出
const AuditLogs = () => {
  const [list, setList] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // 筛选条件
  const [fUsername, setFUsername] = useState("");
  const [fAction, setFAction] = useState<AuditAction | undefined>(undefined);
  const [fSource, setFSource] = useState<AuditSource | undefined>(undefined);
  const [fStatus, setFStatus] = useState<AuditStatus | undefined>(undefined);
  const [fResourceType, setFResourceType] = useState("");
  const [fPath, setFPath] = useState("");
  const [fStart, setFStart] = useState("");
  const [fEnd, setFEnd] = useState("");
  // 详情抽屉
  const [detail, setDetail] = useState<AuditLog | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const buildQuery = useCallback((): AuditLogQuery => {
    const q: AuditLogQuery = {
      page,
      page_size: pageSize,
    };
    if (fUsername.trim()) q.username = fUsername.trim();
    if (fAction) q.action = fAction;
    if (fSource) q.source = fSource;
    if (fStatus) q.status = fStatus;
    if (fResourceType.trim()) q.resource_type = fResourceType.trim();
    if (fPath.trim()) q.path = fPath.trim();
    if (fStart.trim()) q.start = fStart.trim();
    if (fEnd.trim()) q.end = fEnd.trim();
    return q;
  }, [
    page,
    pageSize,
    fUsername,
    fAction,
    fSource,
    fStatus,
    fResourceType,
    fPath,
    fStart,
    fEnd,
  ]);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listAuditLogs(buildQuery());
      setList(data.items);
      setTotal(data.total);
    } catch (err) {
      message.error(errMsg(err, "加载审计日志失败"));
    } finally {
      setLoading(false);
    }
  }, [buildQuery]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  // 重置筛选后回到第一页并加载
  const handleReset = () => {
    setFUsername("");
    setFAction(undefined);
    setFSource(undefined);
    setFStatus(undefined);
    setFResourceType("");
    setFPath("");
    setFStart("");
    setFEnd("");
    setPage(1);
  };

  // 触发筛选查询
  const handleSearch = () => {
    setPage(1);
    void loadLogs();
  };

  // 打开详情抽屉
  const openDetail = async (id: number) => {
    setDetailLoading(true);
    setDetail(null);
    try {
      const data = await retrieveAuditLog(id);
      setDetail(data);
    } catch (err) {
      message.error(errMsg(err, "加载详情失败"));
    } finally {
      setDetailLoading(false);
    }
  };

  // 导出 CSV
  const handleExport = async () => {
    setExporting(true);
    try {
      // 导出时使用当前筛选条件但不分页
      const q: AuditLogQuery = { ...buildQuery() };
      delete q.page;
      delete q.page_size;
      await exportAuditLogs(q);
      message.success("已开始导出");
    } catch (err) {
      message.error(errMsg(err, "导出失败"));
    } finally {
      setExporting(false);
    }
  };

  const columns: ColumnsType<AuditLog> = useMemo(
    () => [
      { title: "ID", dataIndex: "id", width: 70 },
      {
        title: "时间",
        dataIndex: "created_at",
        width: 180,
        render: (v: string) => <Text>{formatTime(v)}</Text>,
      },
      {
        title: "用户",
        dataIndex: "username",
        width: 120,
        render: (v: string) => v || <Text type="secondary">匿名</Text>,
      },
      {
        title: "动作",
        dataIndex: "action",
        width: 140,
        render: (a: AuditAction) => (
          <Tag color={actionColor[a]}>{actionLabel[a] ?? a}</Tag>
        ),
      },
      {
        title: "来源",
        dataIndex: "source",
        width: 90,
        render: (s: AuditSource) => sourceLabel[s] ?? s,
      },
      {
        title: "状态",
        dataIndex: "status",
        width: 80,
        render: (s: AuditStatus) => (
          <Tag color={statusColor[s]}>{statusLabel[s] ?? s}</Tag>
        ),
      },
      {
        title: "方法",
        dataIndex: "method",
        width: 70,
      },
      {
        title: "路径",
        dataIndex: "path",
        ellipsis: true,
        render: (v: string) => (
          <Tooltip title={v}>
            <Text style={{ wordBreak: "break-all" }}>{v || "—"}</Text>
          </Tooltip>
        ),
      },
      {
        title: "资源",
        key: "resource",
        width: 140,
        render: (_, r) =>
          r.resource_type ? (
            <Text>
              {r.resource_type}
              {r.resource_id ? `#${r.resource_id}` : ""}
            </Text>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
      {
        title: "数据源",
        dataIndex: "datasource_name",
        width: 120,
        render: (v: string) => v || <Text type="secondary">—</Text>,
      },
      {
        title: "耗时(ms)",
        dataIndex: "elapsed_ms",
        width: 90,
        render: (v: number | null) => (v !== null && v !== undefined ? v : "—"),
      },
      {
        title: "操作",
        key: "action_btn",
        width: 90,
        render: (_, r) => (
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => openDetail(r.id)}
          >
            详情
          </Button>
        ),
      },
    ],
    []
  );

  const pagination: TablePaginationConfig = {
    current: page,
    pageSize,
    total,
    showSizeChanger: true,
    showTotal: (t) => `共 ${t} 条`,
    onChange: (p, ps) => {
      setPage(p);
      setPageSize(ps);
    },
  };

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }} size={[8, 8]}>
        <Input
          placeholder="用户名"
          allowClear
          value={fUsername}
          onChange={(e) => setFUsername(e.target.value)}
          style={{ width: 140 }}
          onPressEnter={handleSearch}
        />
        <Select<AuditAction | undefined>
          placeholder="动作"
          allowClear
          value={fAction}
          onChange={(v) => setFAction(v)}
          options={actionOptions}
          style={{ width: 160 }}
        />
        <Select<AuditSource | undefined>
          placeholder="来源"
          allowClear
          value={fSource}
          onChange={(v) => setFSource(v)}
          options={sourceOptions}
          style={{ width: 110 }}
        />
        <Select<AuditStatus | undefined>
          placeholder="状态"
          allowClear
          value={fStatus}
          onChange={(v) => setFStatus(v)}
          options={statusOptions}
          style={{ width: 100 }}
        />
        <Input
          placeholder="资源类型"
          allowClear
          value={fResourceType}
          onChange={(e) => setFResourceType(e.target.value)}
          style={{ width: 140 }}
          onPressEnter={handleSearch}
        />
        <Input
          placeholder="路径"
          allowClear
          value={fPath}
          onChange={(e) => setFPath(e.target.value)}
          style={{ width: 200 }}
          onPressEnter={handleSearch}
        />
        <Input
          placeholder="起始时间（YYYY-MM-DD HH:mm:ss）"
          allowClear
          value={fStart}
          onChange={(e) => setFStart(e.target.value)}
          style={{ width: 240 }}
          onPressEnter={handleSearch}
        />
        <Input
          placeholder="截止时间（YYYY-MM-DD HH:mm:ss）"
          allowClear
          value={fEnd}
          onChange={(e) => setFEnd(e.target.value)}
          style={{ width: 240 }}
          onPressEnter={handleSearch}
        />
        <Button type="primary" onClick={handleSearch}>
          查询
        </Button>
        <Button onClick={handleReset}>重置</Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void loadLogs()}
          loading={loading}
        >
          刷新
        </Button>
        <Button
          icon={<ExportOutlined />}
          loading={exporting}
          onClick={handleExport}
        >
          导出 CSV
        </Button>
      </Space>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={pagination}
        locale={{ emptyText: "暂无审计日志" }}
        scroll={{ x: 1400 }}
      />
      <Drawer
        title="审计日志详情"
        width={640}
        open={detail !== null || detailLoading}
        onClose={() => {
          setDetail(null);
          setDetailLoading(false);
        }}
        destroyOnClose
      >
        {detailLoading ? (
          <div style={{ textAlign: "center", padding: 48 }}>
            <Spin />
          </div>
        ) : detail ? (
          <>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="ID">{detail.id}</Descriptions.Item>
              <Descriptions.Item label="时间">
                {formatTime(detail.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="用户">
                {detail.username || "匿名"}
              </Descriptions.Item>
              <Descriptions.Item label="用户 ID">
                {detail.user_id ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="动作" span={2}>
                <Tag color={actionColor[detail.action]}>
                  {actionLabel[detail.action] ?? detail.action}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {sourceLabel[detail.source] ?? detail.source}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColor[detail.status]}>
                  {statusLabel[detail.status] ?? detail.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="方法">
                {detail.method || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="耗时(ms)">
                {detail.elapsed_ms ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="路径" span={2}>
                {detail.path || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="资源类型">
                {detail.resource_type || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="资源 ID">
                {detail.resource_id || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="数据源">
                {detail.datasource_name || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="数据源 ID">
                {detail.datasource_id ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="影响行数">
                {detail.row_count ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="IP">
                {detail.ip ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="User-Agent" span={2}>
                {detail.user_agent || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="错误信息" span={2}>
                {detail.error_message || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="扩展信息" span={2}>
                {Object.keys(detail.extra).length === 0
                  ? "—"
                  : JSON.stringify(detail.extra)}
              </Descriptions.Item>
            </Descriptions>
            {detail.sql && (
              <div style={{ marginTop: 16 }}>
                <Text strong>SQL：</Text>
                <Paragraph
                  style={{
                    marginTop: 8,
                    padding: 8,
                    background: "#f5f5f5",
                    borderRadius: 4,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    maxHeight: 320,
                    overflow: "auto",
                  }}
                >
                  {detail.sql}
                </Paragraph>
              </div>
            )}
          </>
        ) : null}
      </Drawer>
    </>
  );
};

export default AuditLogs;
