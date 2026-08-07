import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    Dropdown,
    Modal,
    message,
} from "antd";
import type { MenuProps } from "antd";
import {
    PlusOutlined,
    PlayCircleOutlined,
    DownloadOutlined,
    HistoryOutlined,
} from "@ant-design/icons";
import Editor from "@monaco-editor/react";
import type { ColumnsType } from "antd/es/table";
import { listDatasources } from "@/api/datasources";
import { executeSql, explainSql, exportSqlResult } from "@/api/manager";
import { useAuthStore } from "@/store/auth";
import { isDesignerOrAdmin } from "@/utils/permission";
import type {
    DataSource,
    EngineType,
    ExplainResult,
    SqlExportFormat,
    SqlResult,
} from "@/types";

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

// 持久化的 Tab 形状（仅保留 SQL 内容与元数据，不存执行结果）
interface PersistedTab {
    key: string;
    title: string;
    sql: string;
}

// 历史执行记录
interface SqlHistoryEntry {
    sql: string;
    executedAt: string; // ISO 时间
    success: boolean;
}

// Monaco 选区最小结构（避免引入 monaco-editor 类型依赖）
interface SelectionLike {
    isEmpty(): boolean;
    startLineNumber: number;
    startColumn: number;
    endLineNumber: number;
    endColumn: number;
}

// Monaco 编辑器引用所需的最小接口
interface MonacoEditorRef {
    getValue: () => string;
    getSelection: () => SelectionLike | null;
    getModel: () => { getValueInRange(range: SelectionLike): string } | null;
    addCommand: (keybinding: number, handler: () => void) => void;
}

// 初始 SQL 模板
const DEFAULT_SQL = "-- 输入 SQL 后点击执行\nSELECT * FROM users LIMIT 10;\n";

// 历史记录上限
const HISTORY_LIMIT = 50;
// 自动追加的 LIMIT 行数
const DEFAULT_LIMIT = 1000;

// localStorage 键名（按数据源 ID 分键）
const tabsStorageKey = (dsId: number): string => `rdbase:sqlTabs:${dsId}`;
const historyStorageKey = (dsId: number): string => `rdbase:sqlHistory:${dsId}`;

// 安全读取 localStorage（解析失败返回 fallback）
const safeRead = <T,>(key: string, fallback: T): T => {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    try {
        return JSON.parse(raw) as T;
    } catch {
        return fallback;
    }
};

// 安全写入 localStorage（配额满或隐私模式静默忽略）
const safeWrite = (key: string, value: unknown): void => {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // 忽略
    }
};

// 加载指定数据源的持久化 Tab（无记录则返回单个默认 Tab）
const loadPersistedTabs = (dsId: number): PersistedTab[] => {
    const tabs = safeRead<PersistedTab[]>(tabsStorageKey(dsId), []);
    if (tabs.length === 0) {
        return [{ key: nextTabKey(), title: "查询 1", sql: DEFAULT_SQL }];
    }
    return tabs;
};

// 加载指定数据源的历史记录
const loadPersistedHistory = (dsId: number): SqlHistoryEntry[] =>
    safeRead<SqlHistoryEntry[]>(historyStorageKey(dsId), []);

// 检测 SQL 是否为 SELECT/WITH 查询且未含 LIMIT 子句
const isSelectWithoutLimit = (sql: string): boolean => {
    // 去除行注释与块注释后判断
    const cleaned = sql
        .replace(/--[^\n]*/g, "")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .trim();
    if (!cleaned) return false;
    const isQuery = /^\s*(SELECT|WITH)\b/i.test(cleaned);
    if (!isQuery) return false;
    return !/\bLIMIT\s+\d+/i.test(cleaned);
};

// 追加 LIMIT 子句（去除末尾分号后追加，再补回分号）
const appendLimit = (sql: string, limit: number): string => {
    const trimmed = sql.replace(/;\s*$/, "");
    return `${trimmed} LIMIT ${limit};`;
};

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

// SQL 控制台页：多 Tab + Monaco + 执行 + 结果表格 + 执行计划 + 历史持久化
const SqlConsole = () => {
    const user = useAuthStore((state) => state.user);
    const canWrite = isDesignerOrAdmin(user);

    const [datasources, setDatasources] = useState<DataSource[]>([]);
    const [selectedDsId, setSelectedDsId] = useState<number | null>(null);
    const [tabs, setTabs] = useState<QueryTab[]>([makeTab("查询 1")]);
    const [activeKey, setActiveKey] = useState<string>(tabs[0].key);
    const [analyze, setAnalyze] = useState(false);
    const [history, setHistory] = useState<SqlHistoryEntry[]>([]);
    // LIMIT 保护开关（默认开启）：对无 LIMIT 的 SELECT 执行前询问是否追加
    const [limitGuard, setLimitGuard] = useState(true);

    // Monaco editor 引用（按 tabKey 索引）
    const editorRefs = useRef<Record<string, MonacoEditorRef | undefined>>({});
    // 当前 tabs 所属的数据源 ID（用于持久化时写入正确的键）
    const tabsDsIdRef = useRef<number | null>(null);
    // 最新 tabs 与 handleExecute 的引用（供 Monaco 快捷键回调使用，避免闭包过期）
    const tabsRef = useRef(tabs);
    const handleExecuteRef = useRef<(tab: QueryTab) => void>(() => { });

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

    // 数据源切换时加载持久化的 Tab 与历史记录
    useEffect(() => {
        if (selectedDsId == null) return;
        tabsDsIdRef.current = selectedDsId;
        const persisted = loadPersistedTabs(selectedDsId);
        const restored: QueryTab[] = persisted.map((t) => ({
            key: t.key,
            title: t.title,
            sql: t.sql,
            result: null,
            explain: null,
            loading: false,
            explainLoading: false,
            error: null,
        }));
        setTabs(restored);
        setActiveKey(restored[0].key);
        setHistory(loadPersistedHistory(selectedDsId));
    }, [selectedDsId]);

    // tabs 变化时持久化（写入 tabsDsIdRef 指向的数据源键）
    useEffect(() => {
        if (tabsDsIdRef.current == null) return;
        const persisted: PersistedTab[] = tabs.map((t) => ({
            key: t.key,
            title: t.title,
            sql: t.sql,
        }));
        safeWrite(tabsStorageKey(tabsDsIdRef.current), persisted);
    }, [tabs]);

    // history 变化时持久化
    useEffect(() => {
        if (tabsDsIdRef.current == null) return;
        safeWrite(historyStorageKey(tabsDsIdRef.current), history);
    }, [history]);

    // 同步 tabs 与 handleExecute 的最新引用供快捷键回调使用
    useEffect(() => {
        tabsRef.current = tabs;
    });
    useEffect(() => {
        handleExecuteRef.current = handleExecute;
    });

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

    // 追加历史记录（保留最近 HISTORY_LIMIT 条）
    const addHistory = (entry: SqlHistoryEntry) => {
        setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
    };

    // 执行 SQL（支持选中片段执行、LIMIT 保护、历史记录）
    const handleExecute = (tab: QueryTab) => {
        const dsId = selectedDsId;
        if (dsId == null) {
            message.warning("请先选择数据源");
            return;
        }
        const editor = editorRefs.current[tab.key];
        let sql: string;
        if (editor) {
            // 选中片段优先：选区非空时执行选中部分而非全文
            const selection = editor.getSelection();
            const selectedText =
                selection && !selection.isEmpty()
                    ? editor.getModel()?.getValueInRange(selection) ?? ""
                    : "";
            sql = selectedText.trim() ? selectedText : editor.getValue();
        } else {
            sql = tab.sql;
        }
        if (!sql.trim()) {
            message.warning("SQL 不能为空");
            return;
        }

        // 内部执行函数（带历史记录）
        const run = async (finalSql: string) => {
            updateTab(tab.key, { loading: true, error: null });
            let success = false;
            try {
                const result = await executeSql(dsId, { sql: finalSql });
                updateTab(tab.key, { result, explain: null, loading: false });
                success = true;
            } catch (err) {
                const msg = errMsg(err, "执行失败");
                updateTab(tab.key, { loading: false, error: msg });
                message.error(msg);
            } finally {
                addHistory({ sql: finalSql, executedAt: new Date().toISOString(), success });
            }
        };

        // LIMIT 保护：对无 LIMIT 的 SELECT 询问是否追加
        if (limitGuard && isSelectWithoutLimit(sql)) {
            Modal.confirm({
                title: "未检测到 LIMIT 子句",
                content: `此 SELECT 语句未包含 LIMIT，是否自动追加 LIMIT ${DEFAULT_LIMIT} 后执行？`,
                okText: "追加并执行",
                cancelText: "原样执行",
                onOk: () => {
                    void run(appendLimit(sql, DEFAULT_LIMIT));
                },
                onCancel: () => {
                    void run(sql);
                },
            });
            return;
        }
        void run(sql);
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

    // 导出当前 Tab 的 SQL 结果集（强制只读，DDL/DML 会被后端拒绝）
    const handleExportSql = async (tab: QueryTab, format: SqlExportFormat) => {
        if (selectedDsId == null) {
            message.warning("请先选择数据源");
            return;
        }
        const sql = editorRefs.current[tab.key]?.getValue() ?? tab.sql;
        if (!sql.trim()) {
            message.warning("SQL 不能为空");
            return;
        }
        try {
            const blob = await exportSqlResult(selectedDsId, { sql, format });
            const filename = `query_result.${format === "xlsx" ? "xlsx" : format}`;
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            message.success(`已导出 ${filename}`);
        } catch (err) {
            message.error(errMsg(err, "导出失败"));
        }
    };

    // 导出格式菜单项
    const buildExportMenu = (tab: QueryTab): MenuProps => ({
        items: [
            { key: "csv", label: "CSV (.csv)" },
            { key: "json", label: "JSON (.json)" },
            { key: "xlsx", label: "Excel (.xlsx)" },
        ],
        onClick: ({ key }: { key: string }) => {
            void handleExportSql(tab, key as SqlExportFormat);
        },
    });

    // 历史记录菜单（含清空选项，点击条目回填到当前 Tab）
    const buildHistoryMenu = (): MenuProps => ({
        items: [
            ...(history.length > 0
                ? [
                    {
                        key: "__clear",
                        label: <Text type="danger">清空历史</Text>,
                        danger: true,
                    },
                ]
                : []),
            ...history.map((h, idx) => ({
                key: String(idx),
                label: (
                    <div style={{ maxWidth: 480, padding: "4px 0" }}>
                        <div
                            style={{
                                fontSize: 11,
                                color: h.success ? "#52c41a" : "#ff4d4f",
                                marginBottom: 2,
                            }}
                        >
                            {new Date(h.executedAt).toLocaleString()} ·{" "}
                            {h.success ? "成功" : "失败"}
                        </div>
                        <div
                            style={{
                                whiteSpace: "pre-wrap",
                                fontFamily: "monospace",
                                fontSize: 12,
                                maxHeight: 100,
                                overflow: "hidden",
                            }}
                        >
                            {h.sql.slice(0, 300)}
                        </div>
                    </div>
                ),
            })),
        ],
        onClick: ({ key }: { key: string }) => {
            if (key === "__clear") {
                setHistory([]);
                return;
            }
            const idx = Number(key);
            const entry = history[idx];
            if (entry && activeTab) {
                updateTab(activeTab.key, { sql: entry.sql });
                message.success("已回填到当前 Tab");
            }
        },
    });

    // Monaco 编辑器挂载时保存引用并注册 Ctrl+Enter 快捷执行
    const handleEditorMount = (
        tabKey: string,
        editorInstance: unknown,
        monacoInstance: unknown
    ) => {
        const editor = editorInstance as MonacoEditorRef;
        const monaco = monacoInstance as {
            KeyMod: { CtrlCmd: number };
            KeyCode: { Enter: number };
        };
        editorRefs.current[tabKey] = editor;
        // Ctrl/Cmd + Enter 快捷执行当前 Tab（优先选中文本）
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
            const tab = tabsRef.current.find((t) => t.key === tabKey);
            if (tab) handleExecuteRef.current(tab);
        });
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
                        <Tooltip title="执行（Ctrl+Enter）">
                            <Button
                                type="primary"
                                icon={<PlayCircleOutlined />}
                                loading={tab.loading}
                                onClick={() => handleExecute(tab)}
                            >
                                执行
                            </Button>
                        </Tooltip>
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
                            {tab.result.columns.length > 0 && (
                                <Dropdown
                                    menu={buildExportMenu(tab)}
                                    trigger={["click"]}
                                    placement="bottomRight"
                                >
                                    <Button
                                        size="small"
                                        icon={<DownloadOutlined />}
                                        title="导出结果集（仅 SELECT）"
                                    >
                                        导出
                                    </Button>
                                </Dropdown>
                            )}
                        </Space>
                    )}
                </div>
                <div style={{ border: "1px solid #f0f0f0", borderRadius: 4 }}>
                    <Editor
                        height="220px"
                        defaultLanguage="sql"
                        value={tab.sql}
                        onChange={(val) => updateTab(tab.key, { sql: val ?? "" })}
                        onMount={(editor, monaco) => handleEditorMount(tab.key, editor, monaco)}
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
                    tabBarExtraContent={
                        <Space>
                            <Tooltip title="开启后，对无 LIMIT 的 SELECT 语句执行前会询问是否追加 LIMIT 1000">
                                <Space size={4}>
                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                        LIMIT 保护
                                    </Text>
                                    <Switch
                                        size="small"
                                        checked={limitGuard}
                                        onChange={setLimitGuard}
                                    />
                                </Space>
                            </Tooltip>
                            <Dropdown
                                menu={buildHistoryMenu()}
                                trigger={["click"]}
                                placement="bottomRight"
                            >
                                <Button
                                    size="small"
                                    icon={<HistoryOutlined />}
                                    disabled={history.length === 0}
                                >
                                    历史 ({history.length})
                                </Button>
                            </Dropdown>
                        </Space>
                    }
                />
            </Content>
        </Layout>
    );
};

export default SqlConsole;
