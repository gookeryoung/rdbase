import { useCallback, useEffect, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
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
  Modal,
  Form,
  InputNumber,
  Popconfirm,
  Upload,
  Tag,
  Badge,
  Drawer,
  message,
} from "antd";
import type { ColumnsType, TableProps } from "antd/es/table";
import type { DataNode } from "antd/es/tree";
import type { MenuProps } from "antd";
import {
  ReloadOutlined,
  ColumnHeightOutlined,
  SearchOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  DownloadOutlined,
  UploadOutlined,
  EyeOutlined,
  FilterOutlined,
  MinusCircleOutlined,
  ClearOutlined,
  TableOutlined,
} from "@ant-design/icons";
import { listDatasources } from "@/api/datasources";
import { listSchemas, listTables, retrieveTable } from "@/api/designer";
import {
  createRow,
  deleteRow,
  exportTable,
  importTable,
  listRows,
  updateRow,
  deleteRoutine,
  deleteTrigger,
  deleteView,
  listRoutines,
  listTriggers,
  listViews,
  retrieveRoutine,
  retrieveTrigger,
  retrieveView,
  updateRoutine,
  updateTrigger,
  updateView,
} from "@/api/manager";
import { useAuthStore } from "@/store/auth";
import { isDesignerOrAdmin } from "@/utils/permission";
import type {
  ColumnMeta,
  DataSource,
  EngineType,
  ExportFormat,
  ForeignKeyMeta,
  IndexMeta,
  NameItem,
  ObjectUpdate,
  RoutineBrief,
  RoutineDetail,
  RoutineKind,
  RowFilter,
  RowFilterOp,
  RowListResponse,
  RowQuery,
  TableBrief,
  TableDetail,
  TriggerBrief,
  TriggerDetail,
  ViewDetail,
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

// 判断值是否为数字（用于表单控件选择 Input vs InputNumber）
const isNumericValue = (val: unknown): boolean =>
  typeof val === "number" || (typeof val === "string" && /^-?\d+(\.\d+)?$/.test(val));

// 高级筛选条件（多列 AND 组合，value 统一为字符串，提交时按操作符转换）
interface AdvancedFilter {
  column: string;
  op: RowFilterOp;
  value: string;
}

// 高级筛选操作符选项（与后端 _COMPARATORS 对齐：eq/ne/gt/lt/ge/le/like/in）
const ADV_FILTER_OPS: { value: RowFilterOp; label: string }[] = [
  { value: "eq", label: "等于 =" },
  { value: "ne", label: "不等于 <>" },
  { value: "gt", label: "大于 >" },
  { value: "ge", label: "大于等于 >=" },
  { value: "lt", label: "小于 <" },
  { value: "le", label: "小于等于 <=" },
  { value: "like", label: "包含 LIKE" },
  { value: "in", label: "包含于 IN" },
];

// 高级筛选 localStorage 键名（按数据源 ID + 表名分键，避免不同表/库互相污染）
const advFiltersStorageKey = (dsId: number, tableName: string): string =>
  `rdbase:advFilters:${dsId}:${tableName}`;

// 安全读取 localStorage（解析失败返回 fallback）
const safeReadAdv = <T,>(key: string, fallback: T): T => {
  const raw = localStorage.getItem(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
};

// 安全写入 localStorage（配额满或隐私模式静默忽略）
const safeWriteAdv = (key: string, value: unknown): void => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 隐私模式或配额满，静默忽略
  }
};

// 将高级筛选条件转换为后端 filters dict（不含列头模糊筛选，由调用方合并）
// - like: 自动包裹 %val%
// - in: 逗号分隔转列表，全数字时转 number[]
// - 比较类: 数字字符串自动转 number
// 空值条件跳过（不生成 filter）
const buildAdvancedFilters = (
  advFilters: AdvancedFilter[]
): Record<string, RowFilter> => {
  const result: Record<string, RowFilter> = {};
  advFilters.forEach(({ column, op, value }) => {
    if (!column || !op) return;
    if (op === "like") {
      const trimmed = value.trim();
      if (trimmed) result[column] = { op: "like", val: `%${trimmed}%` };
    } else if (op === "in") {
      const items = value
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      if (items.length > 0) {
        const allNumeric = items.every((s) => /^-?\d+(\.\d+)?$/.test(s));
        result[column] = {
          op: "in",
          val: allNumeric ? items.map(Number) : items,
        };
      }
    } else {
      const trimmed = value.trim();
      if (!trimmed) return;
      const isNumeric = /^-?\d+(\.\d+)?$/.test(trimmed);
      result[column] = { op, val: isNumeric ? Number(trimmed) : trimmed };
    }
  });
  return result;
};

// Modal 表单模式
type ModalMode = "create" | "edit";

interface ModalState {
  open: boolean;
  mode: ModalMode;
  // 编辑模式下的主键值
  pk: Record<string, unknown> | null;
  // 表单初始值
  initialValues: Record<string, unknown>;
}

// 选中对象类型
type SelectedObject = {
  kind: "view" | "routine" | "trigger";
  schemaName: string | null;
  name: string;
  // routine 子类型
  routineType?: RoutineKind;
  // trigger 关联表（删除时需要）
  triggerTable?: string;
};

// 对象详情 Modal 状态
interface ObjectModalState {
  open: boolean;
  mode: "view" | "edit";
  obj: SelectedObject;
  definition: string;
  // 编辑模式临时持有
  draft: string;
  // 触发器编辑时关联表字段
  table?: string;
}

// 数据库管理页：左侧数据源+表树，右侧数据表格
const Manager = () => {
  const user = useAuthStore((state) => state.user);
  const canEdit = isDesignerOrAdmin(user);

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

  // 对象分组状态：按 schema 缓存
  const [viewsBySchema, setViewsBySchema] = useState<Record<string, NameItem[]>>({});
  const [routinesBySchema, setRoutinesBySchema] = useState<
    Record<string, RoutineBrief[]>
  >({});
  const [triggersBySchema, setTriggersBySchema] = useState<
    Record<string, TriggerBrief[]>
  >({});
  // 对象分组是否已加载（避免重复请求）
  const [objectsLoadedSchema, setObjectsLoadedSchema] = useState<Set<string>>(
    new Set()
  );
  // 选中对象
  const [selectedObject, setSelectedObject] = useState<SelectedObject | null>(
    null
  );
  // 对象详情 Modal
  const [objectModal, setObjectModal] = useState<ObjectModalState | null>(null);
  const [objectModalSubmitting, setObjectModalSubmitting] = useState(false);

  // 数据表格状态
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [pkColumns, setPkColumns] = useState<string[]>([]);
  const [loadingRows, setLoadingRows] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");
  // 列显隐：可见列集合，null 表示全部可见
  const [visibleCols, setVisibleCols] = useState<string[] | null>(null);
  // 列头筛选输入：列名 → 关键词（like 模糊匹配）
  const [filterInputs, setFilterInputs] = useState<Record<string, string>>({});
  // 高级筛选条件列表（多列 AND 组合，多种操作符）
  const [advFilters, setAdvFilters] = useState<AdvancedFilter[]>([]);
  // 高级筛选 Drawer 开关
  const [advFilterOpen, setAdvFilterOpen] = useState(false);
  // 表完整元数据（由 retrieveTable 加载）
  const [tableDetail, setTableDetail] = useState<TableDetail | null>(null);
  // 表结构 Drawer 开关
  const [structureOpen, setStructureOpen] = useState(false);
  // 长字段查看 Modal 状态
  const [longValueModal, setLongValueModal] = useState<{
    column: string;
    value: string;
    isJson: boolean;
  } | null>(null);

  // 编辑/新增 Modal 状态
  const [modalState, setModalState] = useState<ModalState>({
    open: false,
    mode: "create",
    pk: null,
    initialValues: {},
  });
  const [modalSubmitting, setModalSubmitting] = useState(false);
  const [form] = Form.useForm<Record<string, unknown>>();

  // 导入 Modal 状态
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importSubmitting, setImportSubmitting] = useState(false);

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
      setSelectedObject(null);
      setRows([]);
      setColumns([]);
      setPkColumns([]);
      setTotal(0);
      setViewsBySchema({});
      setRoutinesBySchema({});
      setTriggersBySchema({});
      setObjectsLoadedSchema(new Set());
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

  // 加载指定 schema 下的对象（视图/存储过程/触发器）
  const loadObjects = useCallback(
    async (schemaName: string) => {
      if (selectedDsId == null) return;
      try {
        const [views, routines, triggers] = await Promise.all([
          listViews(selectedDsId, schemaName).catch(() => [] as NameItem[]),
          listRoutines(selectedDsId, schemaName).catch(
            () => [] as RoutineBrief[]
          ),
          listTriggers(selectedDsId, schemaName).catch(
            () => [] as TriggerBrief[]
          ),
        ]);
        setViewsBySchema((prev) => ({ ...prev, [schemaName]: views }));
        setRoutinesBySchema((prev) => ({ ...prev, [schemaName]: routines }));
        setTriggersBySchema((prev) => ({ ...prev, [schemaName]: triggers }));
        setObjectsLoadedSchema((prev) => {
          const next = new Set(prev);
          next.add(schemaName);
          return next;
        });
      } catch (err) {
        message.error(errMsg(err, "加载对象列表失败"));
      }
    },
    [selectedDsId]
  );

  // 构造表树节点
  const treeData: DataNode[] = useMemo(() => {
    return schemas.map((s) => {
      const views = viewsBySchema[s.name] ?? [];
      const routines = routinesBySchema[s.name] ?? [];
      const triggers = triggersBySchema[s.name] ?? [];
      const tableCount = (tablesBySchema[s.name] ?? []).length;
      return {
        key: `schema:${s.name}`,
        title: (
          <Space size={4}>
            <Text strong>{s.name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              ({tableCount})
            </Text>
          </Space>
        ),
        selectable: false,
        children: [
          // 表分组
          {
            key: `tables:${s.name}`,
            title: (
              <Space size={4}>
                <Text type="secondary">表</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({tableCount})
                </Text>
              </Space>
            ),
            selectable: false,
            children: (tablesBySchema[s.name] ?? []).map((t) => ({
              key: `table:${s.name}:${t.name}`,
              title: <Text>{t.name}</Text>,
              isLeaf: true,
            })),
          },
          // 视图分组
          {
            key: `views:${s.name}`,
            title: (
              <Space size={4}>
                <Text type="secondary">视图</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({views.length})
                </Text>
              </Space>
            ),
            selectable: false,
            children: views.map((v) => ({
              key: `view:${s.name}:${v.name}`,
              title: <Text>{v.name}</Text>,
              isLeaf: true,
            })),
          },
          // 存储过程/函数分组
          {
            key: `routines:${s.name}`,
            title: (
              <Space size={4}>
                <Text type="secondary">过程/函数</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({routines.length})
                </Text>
              </Space>
            ),
            selectable: false,
            children: routines.map((r) => ({
              key: `routine:${s.name}:${r.type}:${r.name}`,
              title: (
                <Space size={4}>
                  <Text>{r.name}</Text>
                  <Tag
                    color={r.type === "procedure" ? "blue" : "green"}
                    style={{ fontSize: 11, margin: 0 }}
                  >
                    {r.type === "procedure" ? "P" : "F"}
                  </Tag>
                </Space>
              ),
              isLeaf: true,
            })),
          },
          // 触发器分组
          {
            key: `triggers:${s.name}`,
            title: (
              <Space size={4}>
                <Text type="secondary">触发器</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({triggers.length})
                </Text>
              </Space>
            ),
            selectable: false,
            children: triggers.map((t) => ({
              key: `trigger:${s.name}:${t.name}`,
              title: (
                <Space size={4}>
                  <Text>{t.name}</Text>
                  {t.table && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      @{t.table}
                    </Text>
                  )}
                </Space>
              ),
              isLeaf: true,
            })),
          },
        ],
      };
    });
  }, [schemas, tablesBySchema, viewsBySchema, routinesBySchema, triggersBySchema]);

  // 树展开事件：懒加载表与对象
  const handleTreeExpand = (expandedKeys: React.Key[]) => {
    if (selectedDsId == null) return;
    expandedKeys.forEach((key) => {
      const k = String(key);
      if (k.startsWith("schema:")) {
        const schemaName = k.slice("schema:".length);
        if (!tablesBySchema[schemaName]) {
          void loadTables(schemaName);
        }
        if (!objectsLoadedSchema.has(schemaName)) {
          void loadObjects(schemaName);
        }
      }
    });
  };

  // 选中表/对象
  const handleTreeSelect = (keys: React.Key[]) => {
    if (keys.length === 0) {
      setSelectedTable(null);
      setSelectedObject(null);
      return;
    }
    const k = String(keys[0]);
    // 表节点：table:<schema>:<name>
    if (k.startsWith("table:")) {
      const parts = k.split(":");
      if (parts.length < 3) return;
      const schemaName = parts[1];
      const tableName = parts.slice(2).join(":");
      setSelectedObject(null);
      setSelectedTable({ name: tableName, schemaName });
      setPage(1);
      setOrderBy(null);
      setOrderDir("asc");
      setVisibleCols(null);
      setFilterInputs({});
      // 切表时清空内存状态，持久化数据由下方 useEffect 加载
      setAdvFilters([]);
      setTableDetail(null);
      if (selectedDsId != null) {
        retrieveTable(selectedDsId, tableName, schemaName)
          .then((detail) => {
            setPkColumns(detail.primary_key);
            setTableDetail(detail);
          })
          .catch(() => {
            setPkColumns([]);
            setTableDetail(null);
          });
      }
      return;
    }
    // 视图节点：view:<schema>:<name>
    if (k.startsWith("view:")) {
      const parts = k.split(":");
      if (parts.length < 3) return;
      setSelectedTable(null);
      setSelectedObject({
        kind: "view",
        schemaName: parts[1],
        name: parts.slice(2).join(":"),
      });
      return;
    }
    // 例程节点：routine:<schema>:<type>:<name>
    if (k.startsWith("routine:")) {
      const parts = k.split(":");
      if (parts.length < 4) return;
      setSelectedTable(null);
      setSelectedObject({
        kind: "routine",
        schemaName: parts[1],
        routineType: parts[2] as RoutineKind,
        name: parts.slice(3).join(":"),
      });
      return;
    }
    // 触发器节点：trigger:<schema>:<name>
    if (k.startsWith("trigger:")) {
      const parts = k.split(":");
      if (parts.length < 3) return;
      const schemaName = parts[1];
      const name = parts.slice(2).join(":");
      // 关联表从缓存中查
      const triggerBrief = (triggersBySchema[schemaName] ?? []).find(
        (t) => t.name === name
      );
      setSelectedTable(null);
      setSelectedObject({
        kind: "trigger",
        schemaName,
        name,
        triggerTable: triggerBrief?.table,
      });
      return;
    }
    setSelectedTable(null);
    setSelectedObject(null);
  };

  // 打开对象详情/编辑 Modal：拉取定义
  const openObjectModal = async (mode: "view" | "edit") => {
    if (selectedDsId == null || !selectedObject) return;
    const { kind, schemaName, name, routineType, triggerTable } = selectedObject;
    try {
      let definition = "";
      if (kind === "view") {
        const detail: ViewDetail = await retrieveView(
          selectedDsId,
          name,
          schemaName
        );
        definition = detail.definition;
      } else if (kind === "routine") {
        const detail: RoutineDetail = await retrieveRoutine(
          selectedDsId,
          name,
          routineType ?? "function",
          schemaName
        );
        definition = detail.definition;
      } else {
        const detail: TriggerDetail = await retrieveTrigger(
          selectedDsId,
          name,
          schemaName
        );
        definition = detail.definition;
      }
      setObjectModal({
        open: true,
        mode,
        obj: selectedObject,
        definition,
        draft: definition,
        table: kind === "trigger" ? triggerTable ?? undefined : undefined,
      });
    } catch (err) {
      message.error(errMsg(err, "加载对象定义失败"));
    }
  };

  // 提交对象编辑
  const handleObjectModalSubmit = async () => {
    if (selectedDsId == null || !objectModal) return;
    const { obj, draft, table } = objectModal;
    const body: ObjectUpdate = { definition: draft };
    if (obj.kind === "trigger" && table) body.table = table;
    setObjectModalSubmitting(true);
    try {
      if (obj.kind === "view") {
        await updateView(selectedDsId, obj.name, body, obj.schemaName);
      } else if (obj.kind === "routine") {
        await updateRoutine(
          selectedDsId,
          obj.name,
          body,
          obj.routineType ?? "function",
          obj.schemaName
        );
      } else {
        await updateTrigger(selectedDsId, obj.name, body, obj.schemaName);
      }
      message.success("保存成功");
      setObjectModal(null);
      // 刷新对象列表（定义可能变化）
      if (obj.schemaName) void loadObjects(obj.schemaName);
    } catch (err) {
      message.error(errMsg(err, "保存失败"));
    } finally {
      setObjectModalSubmitting(false);
    }
  };

  // 删除对象
  const handleDeleteObject = async () => {
    if (selectedDsId == null || !selectedObject) return;
    const { kind, schemaName, name, routineType, triggerTable } = selectedObject;
    try {
      if (kind === "view") {
        await deleteView(selectedDsId, name, schemaName);
      } else if (kind === "routine") {
        await deleteRoutine(
          selectedDsId,
          name,
          routineType ?? "function",
          schemaName
        );
      } else {
        await deleteTrigger(selectedDsId, name, schemaName, triggerTable);
      }
      message.success("已删除");
      setSelectedObject(null);
      if (schemaName) void loadObjects(schemaName);
    } catch (err) {
      message.error(errMsg(err, "删除失败"));
    }
  };

  // 加载行数据
  const loadRows = useCallback(async () => {
    if (selectedDsId == null || !selectedTable) return;
    setLoadingRows(true);
    // 构造 filters：先放列头模糊匹配（like），再叠加高级筛选（同列时后者覆盖）
    const filters: Record<string, RowFilter> = {};
    Object.entries(filterInputs).forEach(([col, kw]) => {
      if (kw.trim()) {
        filters[col] = { op: "like", val: `%${kw.trim()}%` };
      }
    });
    Object.entries(buildAdvancedFilters(advFilters)).forEach(([col, cond]) => {
      filters[col] = cond;
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
    advFilters,
  ]);

  useEffect(() => {
    void loadRows();
  }, [loadRows]);

  // 切换数据源/表时加载持久化的高级筛选条件
  useEffect(() => {
    if (selectedDsId == null || !selectedTable) {
      setAdvFilters([]);
      return;
    }
    const key = advFiltersStorageKey(selectedDsId, selectedTable.name);
    setAdvFilters(safeReadAdv<AdvancedFilter[]>(key, []));
  }, [selectedDsId, selectedTable]);

  // advFilters 变化时持久化（仅在已选定表时写入，避免清空时机错位覆盖）
  useEffect(() => {
    if (selectedDsId == null || !selectedTable) return;
    const key = advFiltersStorageKey(selectedDsId, selectedTable.name);
    safeWriteAdv(key, advFilters);
  }, [advFilters, selectedDsId, selectedTable]);

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

  // 高级筛选：当前生效的条件数（用于工具栏 Badge 显示）
  const activeAdvFilterCount = useMemo(
    () => Object.keys(buildAdvancedFilters(advFilters)).length,
    [advFilters]
  );

  // 新增一条空的高级筛选条件
  const handleAdvFilterAdd = () => {
    if (columns.length === 0) return;
    setAdvFilters((prev) => [
      ...prev,
      { column: columns[0], op: "eq", value: "" },
    ]);
  };

  // 修改某一条高级筛选条件字段
  const handleAdvFilterChange = (
    idx: number,
    patch: Partial<AdvancedFilter>
  ) => {
    setAdvFilters((prev) =>
      prev.map((f, i) => (i === idx ? { ...f, ...patch } : f))
    );
  };

  // 删除某一条高级筛选条件
  const handleAdvFilterRemove = (idx: number) => {
    setAdvFilters((prev) => prev.filter((_, i) => i !== idx));
    setPage(1);
  };

  // 清空所有高级筛选条件
  const handleAdvFilterClear = () => {
    setAdvFilters([]);
    setPage(1);
  };

  // 从行数据中提取主键 dict
  const extractPk = (row: Record<string, unknown>): Record<string, unknown> => {
    const pk: Record<string, unknown> = {};
    pkColumns.forEach((col) => {
      if (col in row) pk[col] = row[col];
    });
    return pk;
  };

  // 打开新增 Modal
  const handleOpenCreate = () => {
    if (pkColumns.length === 0) {
      message.warning("该表无主键，新增后无法回查定位");
    }
    // 初始值：所有列都为空，主键列若为自增可留空
    const initialValues: Record<string, unknown> = {};
    columns.forEach((col) => {
      initialValues[col] = null;
    });
    setModalState({ open: true, mode: "create", pk: null, initialValues });
    form.setFieldsValue(initialValues as never);
  };

  // 打开编辑 Modal
  const handleOpenEdit = (row: Record<string, unknown>) => {
    if (pkColumns.length === 0) {
      message.warning("该表无主键，无法定位行");
      return;
    }
    const pk = extractPk(row);
    // 编辑表单只显示非主键列
    const initialValues: Record<string, unknown> = {};
    columns.forEach((col) => {
      if (!pkColumns.includes(col)) {
        initialValues[col] = row[col] ?? null;
      }
    });
    setModalState({ open: true, mode: "edit", pk, initialValues });
    form.setFieldsValue(initialValues as never);
  };

  // Modal 提交
  const handleModalSubmit = async () => {
    if (selectedDsId == null || !selectedTable) return;
    let values: Record<string, unknown>;
    try {
      // 触发表单校验（空值允许，仅校验类型）
      values = await form.validateFields();
    } catch {
      return;
    }
    // 过滤掉 null/undefined/空字符串（避免覆盖默认值）
    const cleaned: Record<string, unknown> = {};
    Object.entries(values).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== "") {
        cleaned[k] = v;
      }
    });
    setModalSubmitting(true);
    try {
      if (modalState.mode === "create") {
        await createRow(selectedDsId, selectedTable.name, {
          values: cleaned,
        }, selectedTable.schemaName);
        message.success("新增成功");
      } else if (modalState.pk) {
        await updateRow(selectedDsId, selectedTable.name, modalState.pk, {
          values: cleaned,
        }, selectedTable.schemaName);
        message.success("更新成功");
      }
      setModalState((prev) => ({ ...prev, open: false }));
      await loadRows();
    } catch (err) {
      message.error(errMsg(err, modalState.mode === "create" ? "新增失败" : "更新失败"));
    } finally {
      setModalSubmitting(false);
    }
  };

  // 删除行
  const handleDelete = async (row: Record<string, unknown>) => {
    if (selectedDsId == null || !selectedTable) return;
    if (pkColumns.length === 0) {
      message.warning("该表无主键，无法定位行");
      return;
    }
    const pk = extractPk(row);
    try {
      await deleteRow(selectedDsId, selectedTable.name, pk, selectedTable.schemaName);
      message.success("删除成功");
      await loadRows();
    } catch (err) {
      message.error(errMsg(err, "删除失败"));
    }
  };

  // 导出表数据：触发浏览器下载
  const handleExport = async (format: ExportFormat) => {
    if (selectedDsId == null || !selectedTable) return;
    try {
      const blob = await exportTable(
        selectedDsId,
        selectedTable.name,
        format,
        selectedTable.schemaName
      );
      const ext = format === "xlsx" ? "xlsx" : format;
      const filename = `${selectedTable.name}.${ext}`;
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

  // 导出格式菜单
  const exportMenu: MenuProps = {
    items: [
      { key: "csv", label: "CSV (.csv)" },
      { key: "xlsx", label: "Excel (.xlsx)" },
      { key: "sql", label: "SQL 脚本 (.sql)" },
    ],
    onClick: ({ key }: { key: string }) =>
      void handleExport(key as ExportFormat),
  };

  // 打开导入 Modal
  const handleOpenImport = () => {
    setImportFile(null);
    setImportOpen(true);
  };

  // 提交导入
  const handleImportSubmit = async () => {
    if (selectedDsId == null || !selectedTable) return;
    if (!importFile) {
      message.warning("请先选择文件");
      return;
    }
    setImportSubmitting(true);
    try {
      const result = await importTable(
        selectedDsId,
        selectedTable.name,
        importFile,
        selectedTable.schemaName
      );
      message.success(`导入成功：新增 ${result.success_count} 行`);
      setImportOpen(false);
      await loadRows();
    } catch (err) {
      message.error(errMsg(err, "导入失败"));
    } finally {
      setImportSubmitting(false);
    }
  };

  // 构造表格列定义
  const tableColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
    const cols: ColumnsType<Record<string, unknown>> = columns.map((col) => ({
      title: (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Space size={4}>
            <Text strong>{col}</Text>
            {pkColumns.includes(col) && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                PK
              </Text>
            )}
          </Space>
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
          const json = JSON.stringify(val);
          const truncated = json.length > 200 ? `${json.slice(0, 200)}...` : json;
          return (
            <Button
              type="link"
              size="small"
              style={{ padding: 0, height: "auto", textAlign: "left" }}
              onClick={() =>
                setLongValueModal({ column: col, value: json, isJson: true })
              }
            >
              {truncated}
            </Button>
          );
        }
        const str = String(val);
        if (str.length > 200) {
          return (
            <Button
              type="link"
              size="small"
              style={{ padding: 0, height: "auto", textAlign: "left" }}
              onClick={() =>
                setLongValueModal({ column: col, value: str, isJson: false })
              }
            >
              {`${str.slice(0, 200)}...`}
            </Button>
          );
        }
        return str;
      },
    }));
    // 操作列（designer+ 可见）
    if (canEdit) {
      cols.push({
        title: "操作",
        key: "__actions__",
        fixed: "right",
        width: 120,
        render: (_val: unknown, row: Record<string, unknown>) => (
          <Space size={4}>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => handleOpenEdit(row)}
              disabled={pkColumns.length === 0}
            >
              编辑
            </Button>
            <Popconfirm
              title="确认删除该行？"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleDelete(row)}
            >
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                disabled={pkColumns.length === 0}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      });
    }
    return cols;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, filterInputs, orderBy, orderDir, pkColumns, canEdit]);

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

  // Modal 表单字段（编辑模式排除主键列）
  const modalFormFields = modalState.mode === "edit"
    ? columns.filter((c) => !pkColumns.includes(c))
    : columns;

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
        {selectedTable ? (
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
                {pkColumns.length === 0 && (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    （无主键，禁用编辑/删除）
                  </Text>
                )}
              </Space>
              <Space>
                {canEdit && (
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={handleOpenCreate}
                  >
                    新增行
                  </Button>
                )}
                {canEdit && (
                  <Button
                    icon={<UploadOutlined />}
                    onClick={handleOpenImport}
                  >
                    导入
                  </Button>
                )}
                <Dropdown menu={exportMenu} trigger={["click"]} placement="bottomRight">
                  <Button icon={<DownloadOutlined />}>导出</Button>
                </Dropdown>
                <Tooltip title="列显隐">
                  <Dropdown
                    menu={columnVisibilityMenu}
                    trigger={["click"]}
                    placement="bottomRight"
                  >
                    <Button icon={<ColumnHeightOutlined />} />
                  </Dropdown>
                </Tooltip>
                <Tooltip title="高级筛选">
                  <Badge count={activeAdvFilterCount} size="small" offset={[-2, 2]}>
                    <Button
                      icon={<FilterOutlined />}
                      onClick={() => setAdvFilterOpen(true)}
                    />
                  </Badge>
                </Tooltip>
                <Tooltip title="表结构">
                  <Button
                    icon={<TableOutlined />}
                    onClick={() => setStructureOpen(true)}
                  />
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
        ) : selectedObject ? (
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
                <Tag
                  color={
                    selectedObject.kind === "view"
                      ? "cyan"
                      : selectedObject.kind === "routine"
                        ? "geekblue"
                        : "orange"
                  }
                >
                  {selectedObject.kind === "view"
                    ? "视图"
                    : selectedObject.kind === "routine"
                      ? selectedObject.routineType === "procedure"
                        ? "存储过程"
                        : "函数"
                      : "触发器"}
                </Tag>
                <Text strong style={{ fontSize: 16 }}>
                  {selectedObject.name}
                </Text>
                {selectedObject.schemaName && (
                  <Text type="secondary">
                    ({selectedObject.schemaName})
                  </Text>
                )}
                {selectedObject.kind === "trigger" &&
                  selectedObject.triggerTable && (
                    <Text type="secondary">
                      @{selectedObject.triggerTable}
                    </Text>
                  )}
              </Space>
              <Space>
                <Button
                  icon={<EyeOutlined />}
                  onClick={() => void openObjectModal("view")}
                >
                  查看定义
                </Button>
                {canEdit && (
                  <Button
                    type="primary"
                    icon={<EditOutlined />}
                    onClick={() => void openObjectModal("edit")}
                  >
                    编辑
                  </Button>
                )}
                {canEdit && (
                  <Popconfirm
                    title={`确认删除该${selectedObject.kind === "view"
                      ? "视图"
                      : selectedObject.kind === "routine"
                        ? "例程"
                        : "触发器"
                      }？`}
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    cancelText="取消"
                    onConfirm={() => void handleDeleteObject()}
                  >
                    <Button danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            </div>
            <Empty
              description={
                <span>
                  点击「查看定义」浏览 SQL，{canEdit ? "或「编辑」修改定义" : ""}
                </span>
              }
              style={{ marginTop: 80 }}
            />
          </>
        ) : (
          <Empty
            description="请选择左侧表或对象查看数据"
            style={{ marginTop: 120 }}
          />
        )}
      </Content>
      <Modal
        title={
          objectModal
            ? `${objectModal.mode === "view" ? "查看" : "编辑"}：${objectModal.obj.kind === "view"
              ? "视图"
              : objectModal.obj.kind === "routine"
                ? objectModal.obj.routineType === "procedure"
                  ? "存储过程"
                  : "函数"
                : "触发器"
            } ${objectModal.obj.name}`
            : ""
        }
        open={objectModal?.open ?? false}
        onOk={
          objectModal?.mode === "edit"
            ? () => void handleObjectModalSubmit()
            : () => setObjectModal(null)
        }
        onCancel={() => setObjectModal(null)}
        okText={objectModal?.mode === "edit" ? "保存" : "关闭"}
        cancelText="取消"
        confirmLoading={objectModalSubmitting}
        destroyOnClose
        width={780}
        okButtonProps={
          objectModal?.mode === "view" ? { style: { display: "none" } } : undefined
        }
      >
        {objectModal && (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={8}>
              <Text type="secondary">名称：</Text>
              <Text strong>{objectModal.obj.name}</Text>
              {objectModal.obj.schemaName && (
                <Text type="secondary">({objectModal.obj.schemaName})</Text>
              )}
              {objectModal.obj.kind === "trigger" &&
                objectModal.table && (
                  <Text type="secondary">@{objectModal.table}</Text>
                )}
            </Space>
            <div
              style={{
                border: "1px solid #f0f0f0",
                borderRadius: 4,
                overflow: "hidden",
              }}
            >
              <Editor
                height="420px"
                defaultLanguage="sql"
                value={
                  objectModal.mode === "edit"
                    ? objectModal.draft
                    : objectModal.definition
                }
                onChange={(val) =>
                  objectModal.mode === "edit" &&
                  setObjectModal(
                    (prev) => (prev ? { ...prev, draft: val ?? "" } : prev)
                  )
                }
                theme="vs"
                options={{
                  readOnly: objectModal.mode === "view",
                  minimap: { enabled: false },
                  fontSize: 13,
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 2,
                  wordWrap: "on",
                }}
              />
            </div>
            {objectModal.mode === "edit" && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                保存将执行 DROP IF EXISTS + CREATE 事务，请确保定义语句完整且正确。
              </Text>
            )}
          </Space>
        )}
      </Modal>
      <Modal
        title={modalState.mode === "create" ? "新增行" : "编辑行"}
        open={modalState.open}
        onOk={() => void handleModalSubmit()}
        onCancel={() => setModalState((prev) => ({ ...prev, open: false }))}
        confirmLoading={modalSubmitting}
        destroyOnClose
        width={600}
      >
        <Form form={form} layout="vertical" preserve={false}>
          {modalFormFields.map((col) => {
            // 根据初始值类型选择控件：数字用 InputNumber，其他用 Input
            const initVal = modalState.initialValues[col];
            const isNumeric = isNumericValue(initVal);
            return (
              <Form.Item key={col} name={col} label={col}>
                {isNumeric ? <InputNumber style={{ width: "100%" }} /> : <Input />}
              </Form.Item>
            );
          })}
          {modalFormFields.length === 0 && (
            <Text type="secondary">无可编辑列</Text>
          )}
        </Form>
      </Modal>
      <Modal
        title={`导入数据到 ${selectedTable?.name ?? ""}`}
        open={importOpen}
        onOk={() => void handleImportSubmit()}
        onCancel={() => setImportOpen(false)}
        confirmLoading={importSubmitting}
        destroyOnClose
        width={500}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="secondary">
            支持 CSV (.csv) 与 Excel (.xlsx) 文件，表头须与目标表列名一致。导入为事务操作，任一行失败将全部回滚。
          </Text>
          <Upload.Dragger
            accept=".csv,.xlsx"
            maxCount={1}
            beforeUpload={(file) => {
              setImportFile(file);
              return false; // 阻止自动上传，由 Modal 提交触发
            }}
            onRemove={() => setImportFile(null)}
            fileList={importFile ? [importFile as never] : []}
          >
            <p style={{ margin: 0 }}>
              <UploadOutlined style={{ fontSize: 24 }} />
            </p>
            <Text type="secondary">点击或拖拽文件到此区域</Text>
          </Upload.Dragger>
        </Space>
      </Modal>
      <Drawer
        title="高级筛选"
        placement="right"
        width={520}
        open={advFilterOpen}
        onClose={() => setAdvFilterOpen(false)}
        extra={
          <Space>
            <Button
              icon={<ClearOutlined />}
              onClick={handleAdvFilterClear}
              disabled={advFilters.length === 0}
            >
              清空
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleAdvFilterAdd}
              disabled={columns.length === 0}
            >
              添加条件
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            多列条件以 AND 连接；同列上若同时存在列头模糊匹配与高级筛选，高级筛选优先。
            LIKE 自动包裹 %；IN 用英文逗号分隔多个值。条件按数据源+表持久化到本地。
          </Text>
          {advFilters.length === 0 ? (
            <Empty
              description="暂无筛选条件，点击「添加条件」开始"
              style={{ marginTop: 40 }}
            />
          ) : (
            advFilters.map((f, idx) => (
              <div
                key={idx}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 6,
                  width: "100%",
                }}
              >
                <Select
                  style={{ width: 140, flexShrink: 0 }}
                  placeholder="列"
                  value={f.column || undefined}
                  showSearch
                  optionFilterProp="value"
                  onChange={(v) => handleAdvFilterChange(idx, { column: v })}
                  options={columns.map((c) => ({ value: c, label: c }))}
                />
                <Select
                  style={{ width: 130, flexShrink: 0 }}
                  value={f.op}
                  onChange={(v) => handleAdvFilterChange(idx, { op: v })}
                  options={ADV_FILTER_OPS}
                />
                <Input
                  style={{ flex: 1, minWidth: 100 }}
                  placeholder={
                    f.op === "in" ? "值1,值2,值3" : "值"
                  }
                  value={f.value}
                  onChange={(e) =>
                    handleAdvFilterChange(idx, { value: e.target.value })
                  }
                  onPressEnter={() => {
                    setPage(1);
                    void loadRows();
                  }}
                  allowClear
                />
                <Button
                  type="text"
                  danger
                  icon={<MinusCircleOutlined />}
                  onClick={() => handleAdvFilterRemove(idx)}
                  style={{ flexShrink: 0 }}
                />
              </div>
            ))
          )}
          {advFilters.length > 0 && (
            <Button
              type="primary"
              block
              onClick={() => {
                setPage(1);
                void loadRows();
                setAdvFilterOpen(false);
              }}
            >
              应用筛选
            </Button>
          )}
        </Space>
      </Drawer>
      <Drawer
        title={`表结构：${selectedTable?.name ?? ""}`}
        placement="right"
        width={720}
        open={structureOpen}
        onClose={() => setStructureOpen(false)}
      >
        {tableDetail ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {tableDetail.comment && (
              <div>
                <Text type="secondary">表注释：</Text>
                <Text>{tableDetail.comment}</Text>
              </div>
            )}
            <div>
              <Title level={5} style={{ marginTop: 0 }}>
                字段（{tableDetail.columns.length}）
              </Title>
              <Table<ColumnMeta>
                size="small"
                rowKey="name"
                pagination={false}
                scroll={{ x: "max-content" }}
                dataSource={tableDetail.columns}
                columns={[
                  {
                    title: "字段",
                    dataIndex: "name",
                    width: 140,
                    render: (v: string, r) => (
                      <Space size={4}>
                        <Text strong>{v}</Text>
                        {r.primary_key && <Tag color="gold">PK</Tag>}
                      </Space>
                    ),
                  },
                  { title: "类型", dataIndex: "type", width: 140 },
                  {
                    title: "可空",
                    dataIndex: "nullable",
                    width: 60,
                    render: (v: boolean) => (v ? "YES" : "NO"),
                  },
                  {
                    title: "默认",
                    dataIndex: "default",
                    width: 100,
                    render: (v: string | null | undefined) =>
                      v ?? <Text type="secondary">-</Text>,
                  },
                  {
                    title: "属性",
                    key: "attrs",
                    render: (_, r) => (
                      <Space size={4} wrap>
                        {r.autoincrement && <Tag>AUTO</Tag>}
                        {r.unique && <Tag color="blue">UNIQUE</Tag>}
                        {r.comment && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {r.comment}
                          </Text>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            </div>
            {tableDetail.indexes.length > 0 && (
              <div>
                <Title level={5}>索引（{tableDetail.indexes.length}）</Title>
                <Table<IndexMeta>
                  size="small"
                  rowKey="name"
                  pagination={false}
                  scroll={{ x: "max-content" }}
                  dataSource={tableDetail.indexes}
                  columns={[
                    { title: "名称", dataIndex: "name", width: 180 },
                    {
                      title: "唯一",
                      dataIndex: "unique",
                      width: 60,
                      render: (v: boolean) => (v ? "YES" : "NO"),
                    },
                    {
                      title: "列",
                      dataIndex: "columns",
                      render: (v: string[]) => v.join(", "),
                    },
                  ]}
                />
              </div>
            )}
            {tableDetail.foreign_keys.length > 0 && (
              <div>
                <Title level={5}>外键（{tableDetail.foreign_keys.length}）</Title>
                <Table<ForeignKeyMeta>
                  size="small"
                  rowKey={(r) => r.name ?? r.columns.join(",")}
                  pagination={false}
                  scroll={{ x: "max-content" }}
                  dataSource={tableDetail.foreign_keys}
                  columns={[
                    {
                      title: "名称",
                      dataIndex: "name",
                      width: 160,
                      render: (v: string | null) =>
                        v ?? <Text type="secondary">-</Text>,
                    },
                    {
                      title: "本表列",
                      dataIndex: "columns",
                      render: (v: string[]) => v.join(", "),
                    },
                    {
                      title: "引用表",
                      dataIndex: "referred_table",
                      width: 140,
                    },
                    {
                      title: "引用列",
                      dataIndex: "referred_columns",
                      render: (v: string[]) => v.join(", "),
                    },
                  ]}
                />
              </div>
            )}
            {tableDetail.unique_constraints.length > 0 && (
              <div>
                <Title level={5}>
                  唯一约束（{tableDetail.unique_constraints.length}）
                </Title>
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  {tableDetail.unique_constraints.map((cols, idx) => (
                    <div key={idx}>
                      <Tag color="blue">UNIQUE</Tag>
                      <Text>{cols.join(", ")}</Text>
                    </div>
                  ))}
                </Space>
              </div>
            )}
          </Space>
        ) : (
          <Empty description="正在加载表结构..." style={{ marginTop: 60 }} />
        )}
      </Drawer>
      <Modal
        title={
          longValueModal
            ? `字段内容：${longValueModal.column}`
            : "字段内容"
        }
        open={longValueModal != null}
        onCancel={() => setLongValueModal(null)}
        footer={[
          <Button key="close" onClick={() => setLongValueModal(null)}>
            关闭
          </Button>,
        ]}
        width={780}
        destroyOnClose
      >
        {longValueModal && (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={8}>
              <Text type="secondary">长度：</Text>
              <Text>{longValueModal.value.length}</Text>
              {longValueModal.isJson && <Tag color="geekblue">JSON</Tag>}
            </Space>
            <div
              style={{
                border: "1px solid #f0f0f0",
                borderRadius: 4,
                overflow: "hidden",
              }}
            >
              <Editor
                height="420px"
                defaultLanguage={longValueModal.isJson ? "json" : "plaintext"}
                value={longValueModal.value}
                theme="vs"
                options={{
                  readOnly: true,
                  minimap: { enabled: false },
                  fontSize: 13,
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 2,
                  wordWrap: "on",
                }}
              />
            </div>
          </Space>
        )}
      </Modal>
    </Layout>
  );
};

export default Manager;
