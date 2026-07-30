import { useCallback, useMemo, useRef, useState } from "react";
import {
    Layout,
    Select,
    Tabs,
    Button,
    Space,
    Typography,
    Table,
    Empty,
    Tag,
    Tooltip,
    Switch,
    Alert,
    message,
} from "antd";
import { PlusOutlined, PlayCircleOutlined } from "@ant-design/icons";
import Editor from "@monaco-editor/react";
import type { ColumnsType } from "antd/es/table";
import { listDatasources } from "@/api/datasources";
import { executeSql, explainSql } from "@/api/manager";
import { useAuthStore } from "@/store/auth";
import { isDesignerOrAdmin } from "@/utils/permission";
import type { DataSource, EngineType, ExplainResult, SqlResult } from "@/types";

const { Content } = Layout;
const { Text, Paragraph } = Typography;

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

// 单 Tab 的状态
interface QueryTab {
    key: string;
    title: string;
    sql: string;
    // 执行结果
    result: SqlResult | null;
    explain: ExplainResult | null;
    loading: boolean;
    explainLoading: boolean;
    error: string | null;
}

// 初始 SQL 模板
const DEFAULT_SQL = "-- 输入 SQL 后点击执行\nSELECT * FROM users LIMIT 10;\n";

let _tabSeq = 0;
const nextTabKey = (): string => {
    _tabSeq += 1;
    return `tab-${Date.now()}-${_tabSeq}`;
};

const makeTab = (title: string): QueryTab => ({
    key: nextTabKey(),
    title,
    sql: DEFAULT_SQL,
    result: null,
    explain: null,
    loading: false,
    explainLoading: false,
    error: null,
});

// SQL 控制台页：多 Tab + Monaco + 执行 + 结果表格 + 执行计划
const SqlConsole = () => {
    const user = useAuthStore((state) => state.user);
    const canWrite = isDesignerOrAdmin(user);

    const [datasources, setDatasources] = useState<DataSource[]>([]);
    const [selectedDsId, setSelectedDsId] = useState<number | null>(null);
    const [tabs, setTabs] = useState<QueryTab[]>([makeTab("查询 1")]);
    const [activeKey, setActiveKey] = useState<string>(tabs[0].key);
    const [analyze, setAnalyze] = useState(false);
    // Monaco editor 主题（dark 适配深色编辑器）
    const editorRefs = useRef<Record<string, { getValue: () => string } | undefined>>({});

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

    // 仅在挂载时加载一次（避免 selectedDsId 变化重复请求）
    useMemo(() => {
        void loadDatasources();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // 当前活动 Tab
    const activeTab = tabs.find((t) => t.key === activeKey) ?? tabs[0];

    // 更新指定 Tab 状态
    const updateTab = (key: string, patch: Partial<QueryTab>) => {
        setTabs((prev) => prev.map((t) => (t.key === key ? { ...t, ...patch } : t)));
    };

    // 新增 Tab
    const handleAddTab = () => {
        const newTab = makeTab(`查询 ${tabs.length + 1}`);
        setTabs((prev) => [...prev, newTab]);
        setActiveKey(newTab.key);
    };

    // 关闭 Tab
    const handleRemoveTab = (targetKey: string) => {
        setTabs((prev) => {
            const next = prev.filter((t) => t.key !== targetKey);
            if (next.length === 0) {
                // 至少保留一个 Tab
                const fallback = makeTab("查询 1");
                setActiveKey(fallback.key);
                return [fallback];
            }
            if (targetKey === activeKey) {
                setActiveKey(next[next.length - 1].key);
            }
            return next;
        });
    };

    // 执行 SQL
    const handleExecute = async (tab: QueryTab) => {
        if (selectedDsId == null) {
            message.warning("请先选择数据源");
            return;
        }
        const sql = editorRefs.current[tab.key]?.getValue() ?? tab.sql;
        if (!sql.trim()) {
            message.warning("SQL 不能为空");
            return;
        }
        updateTab(tab.key, { loading: true, error: null });
        try {
            const result = await executeSql(selectedDsId, { sql });
            updateTab(tab.key, { result, explain: null, loading: false });
        } catch (err) {
            const msg = errMsg(err, "执行失败");
            updateTab(tab.key, { loading: false, error: msg });
            message.error(msg);
        }
    };

    // 执行计划
    const handleExplain = async (tab: QueryTab) => {
        if (selectedDsId == null) {
            message.warning("请先选择数据源");
            return;
        }
        const sql = editorRefs.current[tab.key]?.getValue() ?? tab.sql;
        if (!sql.trim()) {
            message.warning("SQL 不能为空");
            return;
        }
        updateTab(tab.key, { explainLoading: true, error: null });
        try {
            const explain = await explainSql(selectedDsId, { sql, analyze });
            updateTab(tab.key, { explain, explainLoading: false });
        } catch (err) {
            const msg = errMsg(err, "执行计划获取失败");
            updateTab(tab.key, { explainLoading: false, error: msg });
            message.error(msg);
        }
    };

    // Monaco 编辑器挂载时保存引用
    const handleEditorMount = (tabKey: string, editorInstance: unknown) => {
        editorRefs.current[tabKey] = editorInstance as { getValue: () => string };
    };

    // 构造结果表格列定义
    const resultColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
        if (!activeTab?.result) return [];
        return activeTab.result.columns.map((col) => ({
            title: col,
            dataIndex: col,
            key: col,
            ellipsis: true,
            render: (val: unknown) => {
                if (val === null || val === undefined) return <Text type="secondary">NULL</Text>;
                if (typeof val === "object") return JSON.stringify(val);
                return String(val);
            },
        }));
    }, [activeTab]);

    // 构造执行计划表格列定义
    const explainColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
        if (!activeTab?.explain) return [];
        return activeTab.explain.columns.map((col) => ({
            title: col,
            dataIndex: col,
            key: col,
            ellipsis: true,
            render: (val: unknown) => {
                if (val === null || val === undefined) return <Text type="secondary">NULL</Text>;
                return String(val);
            },
        }));
    }, [activeTab]);

    // Tab 项
    const tabItems = tabs.map((tab) => ({
        key: tab.key,
        label: (
            <Space size={4}>
                <Text>{tab.title}</Text>
                {tab.loading && <Tag color="processing">执行中</Tag>}
            </Space>
        ),
        closable: true,
        children: (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <Space>
                        <Button
                            type="primary"
                            icon={<PlayCircleOutlined />}
                            loading={tab.loading}
                            onClick={() => void handleExecute(tab)}
                        >
                            执行
                        </Button>
                        <Tooltip title="获取执行计划（EXPLAIN）">
                            <Button
                                loading={tab.explainLoading}
                                onClick={() => void handleExplain(tab)}
                            >
                                执行计划
                            </Button>
                        </Tooltip>
                        <Space size={4}>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                                ANALYZE
                            </Text>
                            <Switch
                                size="small"
                                checked={analyze}
                                onChange={setAnalyze}
                                disabled={!canWrite}
                            />
                            {!canWrite && (
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                    （SQLite 不支持）
                                </Text>
                            )}
                        </Space>
                    </Space>
                    {tab.result && (
                        <Space>
                            <Tag color={tab.result.read_only ? "blue" : "orange"}>
                                {tab.result.read_only ? "只读" : "写操作"}
                            </Tag>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                                影响/返回 {tab.result.rowcount} 行 · 耗时 {tab.result.elapsed_ms} ms
                            </Text>
                        </Space>
                    )}
                </div>
                <div style={{ border: "1px solid #f0f0f0", borderRadius: 4 }}>
                    <Editor
                        height="220px"
                        defaultLanguage="sql"
                        value={tab.sql}
                        onChange={(val) => updateTab(tab.key, { sql: val ?? "" })}
                        onMount={(editor) => handleEditorMount(tab.key, editor)}
                        theme="vs"
                        options={{
                            minimap: { enabled: false },
                            fontSize: 13,
                            scrollBeyondLastLine: false,
                            automaticLayout: true,
                            tabSize: 2,
                        }}
                    />
                </div>
                {tab.error && (
                    <Alert
                        type="error"
                        message="执行错误"
                        description={tab.error}
                        showIcon
                        closable
                        onClose={() => updateTab(tab.key, { error: null })}
                    />
                )}
                {tab.result ? (
                    <div>
                        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                            结果
                        </Paragraph>
                        {tab.result.columns.length > 0 ? (
                            <Table
                                rowKey={(_, idx) => String(idx)}
                                columns={resultColumns}
                                dataSource={tab.result.rows}
                                size="small"
                                scroll={{ x: "max-content" }}
                                pagination={
                                    tab.result.rows.length > 50
                                        ? { pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }
                                        : false
                                }
                            />
                        ) : (
                            <Alert
                                type="success"
                                message={`执行成功，影响 ${tab.result.rowcount} 行`}
                                showIcon
                            />
                        )}
                    </div>
                ) : (
                    !tab.error && (
                        <Empty
                            description="点击执行查看结果"
                            style={{ marginTop: 40 }}
                        />
                    )
                )}
                {tab.explain && (
                    <div>
                        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                            执行计划（{tab.explain.dialect}
                            {tab.explain.analyze ? " + ANALYZE" : ""}）
                        </Paragraph>
                        {tab.explain.columns.length > 0 ? (
                            <Table
                                rowKey={(_, idx) => String(idx)}
                                columns={explainColumns}
                                dataSource={tab.explain.rows}
                                size="small"
                                scroll={{ x: "max-content" }}
                                pagination={false}
                            />
                        ) : (
                            <pre
                                style={{
                                    background: "#fafafa",
                                    padding: 12,
                                    borderRadius: 4,
                                    margin: 0,
                                    fontSize: 12,
                                    whiteSpace: "pre-wrap",
                                }}
                            >
                                {tab.explain.plan.join("\n")}
                            </pre>
                        )}
                    </div>
                )}
            </div>
        ),
    }));

    return (
        <Layout style={{ minHeight: "calc(100vh - 112px)", background: "transparent" }}>
            <Content style={{ background: "#fff", borderRadius: 8, padding: 16 }}>
                <div
                    style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: 12,
                    }}
                >
                    <Space>
                        <Text strong>数据源</Text>
                        <Select
                            placeholder="选择数据源"
                            value={selectedDsId ?? undefined}
                            onChange={(v) => setSelectedDsId(v)}
                            style={{ width: 280 }}
                            options={datasources.map((d) => ({
                                value: d.id,
                                label: `${d.name} (${engineLabel[d.engine]})`,
                            }))}
                        />
                    </Space>
                    <Space>
                        {!canWrite && (
                            <Tooltip title="viewer 仅可执行 SELECT；DDL/DML 须 designer+">
                                <Tag color="blue">viewer 只读模式</Tag>
                            </Tooltip>
                        )}
                        {canWrite && <Tag color="green">designer+ 可执行 DDL/DML</Tag>}
                    </Space>
                </div>
                <Tabs
                    type="editable-card"
                    activeKey={activeKey}
                    onChange={setActiveKey}
                    onEdit={(targetKey, action) => {
                        if (action === "add") handleAddTab();
                        else handleRemoveTab(String(targetKey));
                    }}
                    addIcon={<PlusOutlined />}
                    items={tabItems}
                    hideAdd
                    tabBarStyle={{ marginBottom: 12 }}
                />
            </Content>
        </Layout>
    );
};

export default SqlConsole;
