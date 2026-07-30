import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Layout,
  Table,
  Button,
  Space,
  Typography,
  Tree,
  Input,
  Select,
  Dropdown,
  Checkbox,
  Empty,
  Tooltip,
  message,
} from "antd";
import type { ColumnsType, TableProps } from "antd/es/table";
import type { DataNode } from "antd/es/tree";
import type { MenuProps } from "antd";
import {
  ReloadOutlined,
  ColumnHeightOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { listDatasources } from "@/api/datasources";
import { listSchemas, listTables } from "@/api/designer";
import { listRows } from "@/api/manager";
import type {
  DataSource,
  EngineType,
  NameItem,
  RowListResponse,
  RowQuery,
  TableBrief,
} from "@/types";

const { Sider, Content } = Layout;
const { Text, Title } = Typography;

// 引擎中文标签
const engineLabel: Record<EngineType, string> = {
  mysql: "MySQL",
  postgresql: "PostgreSQL",
  sqlite: "SQLite",
};

// 统一提取后端错误信息
const errMsg = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return detail ?? fallback;
};

// 数据库管理页：左侧数据源+表树，右侧数据表格
const Manager = () => {
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [selectedDsId, setSelectedDsId] = useState<number | null>(null);
  const [schemas, setSchemas] = useState<NameItem[]>([]);
  const [tablesBySchema, setTablesBySchema] = useState<
    Record<string, TableBrief[]>
  >({});

  const [selectedTable, setSelectedTable] = useState<{
    name: string;
    schemaName: string | null;
  } | null>(null);

  // 数据表格状态
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [loadingRows, setLoadingRows] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");
  // 列显隐：可见列集合，null 表示全部可见
  const [visibleCols, setVisibleCols] = useState<string[] | null>(null);
  // 列头筛选输入：列名 → 关键词（like 模糊匹配）
  const [filterInputs, setFilterInputs] = useState<Record<string, string>>({});

  // 加载数据源列表
  const loadDatasources = useCallback(async () => {
    try {
      const data = await listDatasources();
      setDatasources(data);
      if (data.length > 0 && selectedDsId === null) {
        setSelectedDsId(data[0].id);
      }
    } catch (err) {
      message.error(errMsg(err, "加载数据源失败"));
    }
  }, [selectedDsId]);

  useEffect(() => {
    void loadDatasources();
  }, [loadDatasources]);

  // 加载 Schema 列表
  const loadSchemas = useCallback(async (dsId: number) => {
    try {
      const data = await listSchemas(dsId);
      setSchemas(data);
      setTablesBySchema({});
    } catch (err) {
      message.error(errMsg(err, "加载 Schema 失败"));
    }
  }, []);

  useEffect(() => {
    if (selectedDsId != null) {
      void loadSchemas(selectedDsId);
      setSelectedTable(null);
      setRows([]);
      setColumns([]);
      setTotal(0);
    }
  }, [selectedDsId, loadSchemas]);

  // 加载指定 schema 下的表
  const loadTables = useCallback(
    async (schemaName: string) => {
      if (selectedDsId == null) return;
      try {
        const data = await listTables(selectedDsId, schemaName);
        setTablesBySchema((prev) => ({ ...prev, [schemaName]: data }));
      } catch (err) {
        message.error(errMsg(err, "加载表列表失败"));
      }
    },
    [selectedDsId]
  );

  // 构造表树节点
  const treeData: DataNode[] = useMemo(() => {
    return schemas.map((s) => ({
      key: `schema:${s.name}`,
      title: (
        <Space size={4}>
          <Text strong>{s.name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            ({(tablesBySchema[s.name] ?? []).length})
          </Text>
        </Space>
      ),
      selectable: false,
      children: (tablesBySchema[s.name] ?? []).map((t) => ({
        key: `table:${s.name}:${t.name}`,
        title: <Text>{t.name}</Text>,
        isLeaf: true,
      })),
    }));
  }, [schemas, tablesBySchema]);

  // 树展开事件：懒加载表
  const handleTreeExpand = (expandedKeys: React.Key[]) => {
    if (selectedDsId == null) return;
    // 对新展开的 schema 触发表加载
    expandedKeys.forEach((key) => {
      const k = String(key);
      if (k.startsWith("schema:")) {
        const schemaName = k.slice("schema:".length);
        if (!tablesBySchema[schemaName]) {
          void loadTables(schemaName);
        }
      }
    });
  };

  // 选中表
  const handleTreeSelect = (keys: React.Key[]) => {
    if (keys.length === 0) {
      setSelectedTable(null);
      return;
    }
    const k = String(keys[0]);
    if (!k.startsWith("table:")) {
      setSelectedTable(null);
      return;
    }
    const parts = k.split(":");
    if (parts.length < 3) return;
    const schemaName = parts[1];
    const tableName = parts.slice(2).join(":");
    setSelectedTable({ name: tableName, schemaName });
    // 重置查询状态
    setPage(1);
    setOrderBy(null);
    setOrderDir("asc");
    setVisibleCols(null);
    setFilterInputs({});
  };

  // 加载行数据
  const loadRows = useCallback(async () => {
    if (selectedDsId == null || !selectedTable) return;
    setLoadingRows(true);
    // 构造 filters：非空关键词转 like 模糊匹配
    const filters: Record<string, { op: "like"; val: string }> = {};
    Object.entries(filterInputs).forEach(([col, kw]) => {
      if (kw.trim()) {
        filters[col] = { op: "like", val: `%${kw.trim()}%` };
      }
    });
    const params: RowQuery = {
      schema_name: selectedTable.schemaName,
      page,
      page_size: pageSize,
      order_by: orderBy,
      order_dir: orderDir,
      columns: visibleCols && visibleCols.length > 0 ? visibleCols : null,
      filters: Object.keys(filters).length > 0 ? filters : null,
    };
    try {
      const data: RowListResponse = await listRows(
        selectedDsId,
        selectedTable.name,
        params
      );
      setRows(data.items);
      setTotal(data.total);
      setColumns(data.columns);
    } catch (err) {
      message.error(errMsg(err, "加载数据失败"));
      setRows([]);
      setTotal(0);
    } finally {
      setLoadingRows(false);
    }
  }, [
    selectedDsId,
    selectedTable,
    page,
    pageSize,
    orderBy,
    orderDir,
    visibleCols,
    filterInputs,
  ]);

  useEffect(() => {
    void loadRows();
  }, [loadRows]);

  // 列头排序点击
  const handleTableChange: TableProps<Record<string, unknown>>["onChange"] = (
    pagination,
    _filters,
    sorter
  ) => {
    if (pagination.current != null) setPage(pagination.current);
    if (pagination.pageSize != null) {
      setPageSize(pagination.pageSize);
      setPage(1);
    }
    const s = Array.isArray(sorter) ? sorter[0] : sorter;
    if (s && s.field != null && s.order) {
      setOrderBy(String(s.field));
      setOrderDir(s.order === "ascend" ? "asc" : "desc");
    } else {
      setOrderBy(null);
      setOrderDir("asc");
    }
  };

  // 列头筛选输入变更
  const handleFilterInputChange = (col: string, value: string) => {
    setFilterInputs((prev) => ({ ...prev, [col]: value }));
    setPage(1);
  };

  // 构造表格列定义
  const tableColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
    return columns.map((col) => ({
      title: (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Text strong>{col}</Text>
          <Input
            size="small"
            allowClear
            placeholder="模糊匹配"
            prefix={<SearchOutlined style={{ color: "#999" }} />}
            value={filterInputs[col] ?? ""}
            onChange={(e) => handleFilterInputChange(col, e.target.value)}
            onPressEnter={() => void loadRows()}
          />
        </div>
      ),
      dataIndex: col,
      key: col,
      ellipsis: true,
      sorter: orderBy === col,
      sortOrder: orderBy === col ? (orderDir === "asc" ? "ascend" : "descend") : null,
      render: (val: unknown) => {
        if (val === null || val === undefined) {
          return <Text type="secondary">NULL</Text>;
        }
        if (typeof val === "boolean") {
          return String(val);
        }
        if (typeof val === "object") {
          return JSON.stringify(val);
        }
        return String(val);
      },
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, filterInputs, orderBy, orderDir]);

  // 列显隐菜单
  const columnVisibilityMenu: MenuProps = {
    items: [
      {
        key: "cols",
        type: "group",
        label: "显示列",
        children: columns.map((c) => ({
          key: c,
          label: (
            <Checkbox
              checked={
                visibleCols == null ? true : visibleCols.includes(c)
              }
              onChange={(e) => {
                const checked = e.target.checked;
                setVisibleCols((prev) => {
                  // 当前全部可见 → 转为显式列表
                  const current = prev == null ? [...columns] : [...prev];
                  if (checked) {
                    if (!current.includes(c)) current.push(c);
                  } else {
                    const idx = current.indexOf(c);
                    if (idx >= 0) current.splice(idx, 1);
                  }
                  // 全选时回退为 null（全部可见）
                  if (current.length === columns.length) return null;
                  return current;
                });
                setPage(1);
              }}
            >
              {c}
            </Checkbox>
          ),
        })),
      },
    ],
  };

  return (
    <Layout style={{ minHeight: "calc(100vh - 112px)", background: "transparent" }}>
      <Sider
        width={320}
        theme="light"
        style={{ background: "#fff", marginRight: 16, borderRadius: 8, overflow: "auto" }}
      >
        <div style={{ padding: 12, borderBottom: "1px solid #f0f0f0" }}>
          <Title level={5} style={{ margin: 0, marginBottom: 8 }}>
            数据库管理
          </Title>
          <Select
            placeholder="选择数据源"
            value={selectedDsId ?? undefined}
            onChange={(v) => setSelectedDsId(v)}
            style={{ width: "100%" }}
            options={datasources.map((d) => ({
              value: d.id,
              label: `${d.name} (${engineLabel[d.engine]})`,
            }))}
          />
        </div>
        {selectedDsId == null ? (
          <Empty description="请选择数据源" style={{ marginTop: 60 }} />
        ) : (
          <Tree
            treeData={treeData}
            onExpand={handleTreeExpand}
            onSelect={handleTreeSelect}
            showLine
            defaultExpandedKeys={
              schemas.length > 0 ? [`schema:${schemas[0].name}`] : []
            }
          />
        )}
      </Sider>
      <Content style={{ background: "#fff", borderRadius: 8, padding: 16, overflow: "auto" }}>
        {!selectedTable ? (
          <Empty
            description="请选择左侧表查看数据"
            style={{ marginTop: 120 }}
          />
        ) : (
          <>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 12,
              }}
            >
              <Space>
                <Text strong style={{ fontSize: 16 }}>
                  {selectedTable.name}
                </Text>
                {selectedTable.schemaName && (
                  <Text type="secondary">({selectedTable.schemaName})</Text>
                )}
                <Text type="secondary">共 {total} 行</Text>
              </Space>
              <Space>
                <Tooltip title="列显隐">
                  <Dropdown
                    menu={columnVisibilityMenu}
                    trigger={["click"]}
                    placement="bottomRight"
                  >
                    <Button icon={<ColumnHeightOutlined />} />
                  </Dropdown>
                </Tooltip>
                <Tooltip title="刷新">
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => void loadRows()}
                  />
                </Tooltip>
              </Space>
            </div>
            <Table
              rowKey={(_, idx) => String(idx)}
              columns={tableColumns}
              dataSource={rows}
              loading={loadingRows}
              size="small"
              scroll={{ x: "max-content" }}
              onChange={handleTableChange}
              pagination={{
                current: page,
                pageSize: pageSize,
                total: total,
                showSizeChanger: true,
                pageSizeOptions: [10, 20, 50, 100],
                showTotal: (t) => `共 ${t} 行`,
              }}
            />
          </>
        )}
      </Content>
    </Layout>
  );
};

export default Manager;
