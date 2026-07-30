import { useCallback, useEffect, useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Position,
  Handle,
  useNodesState,
  useEdgesState,
  addEdge,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  MarkerType,
  ReactFlowProvider,
} from "reactflow";
import "reactflow/dist/style.css";
import { Empty, Tag, Typography, message } from "antd";
import type { Draft, FieldSpec, ForeignKeySpec } from "@/types";

const { Text } = Typography;

// 节点数据：表信息 + 是否当前编辑表
interface TableNodeData {
  tableName: string;
  schemaName: string | null;
  fields: FieldSpec[];
  isCurrent: boolean;
  [key: string]: unknown;
}

// 节点 ID 与 handle ID 编解码
// 节点 ID = `t_<draftId>`
// handle ID = `s_<fieldName>`（source）或 `t_<fieldName>`（target）
const nodeIdOf = (draftId: number) => `t_${draftId}`;
const draftIdFromNode = (nodeId: string): number =>
  Number(nodeId.slice(2));

// 边 ID = `e_<fkIndex>`（与 spec.foreign_keys 索引一一对应）
const edgeIdOf = (fkIndex: number) => `e_${fkIndex}`;
const fkIndexFromEdge = (edgeId: string): number =>
  Number(edgeId.slice(2));

// 字段 handle id → 字段名
const fieldFromHandle = (handleId: string): string =>
  handleId.slice(2);

// 网格布局：每行 3 个节点，间距 320×360
const layoutNodes = (drafts: Draft[], currentId: number | null): Node<TableNodeData>[] => {
  const cols = 3;
  const xGap = 320;
  const yGap = 360;
  return drafts.map((d, idx) => {
    const row = Math.floor(idx / cols);
    const col = idx % cols;
    return {
      id: nodeIdOf(d.id),
      type: "tableNode",
      position: { x: col * xGap, y: row * yGap },
      data: {
        tableName: d.table_name,
        schemaName: d.schema_name,
        fields: d.spec.fields,
        isCurrent: d.id === currentId,
      },
    };
  });
};

// 自定义表节点：表名头 + 字段列表（每行带左右 handle）
const TableNode = ({ data }: NodeProps<TableNodeData>) => {
  return (
    <div
      style={{
        width: 220,
        background: "#fff",
        border: data.isCurrent ? "2px solid #1677ff" : "1px solid #d9d9d9",
        borderRadius: 6,
        fontSize: 12,
        boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
      }}
    >
      <div
        style={{
          padding: "6px 10px",
          background: data.isCurrent ? "#1677ff" : "#fafafa",
          color: data.isCurrent ? "#fff" : "#262626",
          fontWeight: 600,
          borderBottom: "1px solid #e8e8e8",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>{data.tableName}</span>
        {data.isCurrent && <Tag color="white" style={{ color: "#1677ff", margin: 0 }}>编辑中</Tag>}
      </div>
      <div>
        {data.fields.length === 0 ? (
          <div style={{ padding: "8px 10px", color: "#999" }}>（无字段）</div>
        ) : (
          data.fields.map((f) => (
            <div
              key={f.name}
              style={{
                position: "relative",
                padding: "3px 10px",
                borderBottom: "1px solid #f5f5f5",
                color: f.primary_key ? "#1677ff" : "#262626",
                fontWeight: f.primary_key ? 600 : 400,
              }}
            >
              <Handle
                id={`t_${f.name}`}
                type="target"
                position={Position.Left}
                style={{ top: "50%", width: 6, height: 6 }}
              />
              <span>{f.name}</span>
              <span style={{ color: "#999", marginLeft: 6, fontSize: 11 }}>
                {f.type}
              </span>
              <Handle
                id={`s_${f.name}`}
                type="source"
                position={Position.Right}
                style={{ top: "50%", width: 6, height: 6 }}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
};

const nodeTypes = { tableNode: TableNode };

interface ERGraphProps {
  drafts: Draft[]; // 当前数据源下的所有草稿（含当前编辑表）
  currentDraft: Draft | null; // 当前选中编辑的草稿
  canEdit: boolean;
  onAddForeignKey: (fk: ForeignKeySpec) => void;
  onRemoveForeignKey: (index: number) => void;
}

const ERGraphInner = ({
  drafts,
  currentDraft,
  canEdit,
  onAddForeignKey,
  onRemoveForeignKey,
}: ERGraphProps) => {
  // 仅展示与当前草稿同数据源的草稿表
  const visibleDrafts = useMemo(() => {
    if (!currentDraft) return [];
    return drafts.filter((d) => d.datasource_id === currentDraft.datasource_id);
  }, [drafts, currentDraft]);

  // 节点
  const initialNodes = useMemo(
    () => layoutNodes(visibleDrafts, currentDraft?.id ?? null),
    [visibleDrafts, currentDraft]
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);

  // 节点随 visibleDrafts 变化重建（保留已拖拽位置：通过 id 复用）
  useEffect(() => {
    setNodes((prev) => {
      const prevPos = new Map(prev.map((n) => [n.id, n.position]));
      return layoutNodes(visibleDrafts, currentDraft?.id ?? null).map((n) => ({
        ...n,
        position: prevPos.get(n.id) ?? n.position,
      }));
    });
  }, [visibleDrafts, currentDraft, setNodes]);

  // 边：根据当前草稿的 foreign_keys 生成
  const initialEdges = useMemo<Edge[]>(() => {
    if (!currentDraft) return [];
    const fks = currentDraft.spec.foreign_keys;
    const draftByTable = new Map(visibleDrafts.map((d) => [d.table_name, d]));
    return fks
      .map((fk, idx): Edge | null => {
        const sourceDraft = currentDraft; // 外键源表 = 当前草稿
        const targetDraft = draftByTable.get(fk.referred_table);
        if (!targetDraft) return null;
        const sourceField = fk.columns[0];
        const targetField = fk.referred_columns[0];
        return {
          id: edgeIdOf(idx),
          source: nodeIdOf(sourceDraft.id),
          sourceHandle: `s_${sourceField}`,
          target: nodeIdOf(targetDraft.id),
          targetHandle: `t_${targetField}`,
          label: fk.name || `FK${idx + 1}`,
          labelStyle: { fontSize: 11 },
          labelBgStyle: { fill: "#f0f5ff" },
          style: { stroke: "#1677ff", strokeWidth: 1.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#1677ff" },
        };
      })
      .filter((e): e is Edge => e !== null);
  }, [currentDraft, visibleDrafts]);

  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // 外键变化时同步边（保留用户布局）
  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

  // 拖拽连接：仅在源表 = 当前编辑表时创建外键
  const onConnect = useCallback(
    (connection: Connection) => {
      if (!currentDraft || !canEdit) return;
      if (!connection.source || !connection.target) return;
      const sourceDraftId = draftIdFromNode(connection.source);
      if (sourceDraftId !== currentDraft.id) {
        message.warning("只能在当前编辑的表上创建外键（请从「编辑中」表节点拖出）");
        return;
      }
      const sourceField = fieldFromHandle(connection.sourceHandle ?? "");
      const targetDraftId = draftIdFromNode(connection.target);
      const targetDraft = visibleDrafts.find((d) => d.id === targetDraftId);
      if (!targetDraft) return;
      const targetField = fieldFromHandle(connection.targetHandle ?? "");
      if (!sourceField || !targetField) return;

      const fk: ForeignKeySpec = {
        name: null,
        columns: [sourceField],
        referred_table: targetDraft.table_name,
        referred_columns: [targetField],
        on_delete: "RESTRICT",
      };
      onAddForeignKey(fk);
      setEdges((eds) => addEdge({ ...connection, animated: false }, eds));
      message.success(`已新增外键：${sourceField} → ${targetDraft.table_name}.${targetField}`);
    },
    [currentDraft, canEdit, visibleDrafts, onAddForeignKey, setEdges]
  );

  // 删除边：按 Backspace/Delete 键删除选中的边，同步移除外键
  const onEdgesDelete = useCallback(
    (deletedEdges: Edge[]) => {
      if (!currentDraft || !canEdit) return;
      // 按 fkIndex 降序删除，避免索引错位
      const indices = deletedEdges
        .map((e) => fkIndexFromEdge(e.id))
        .filter((i) => !Number.isNaN(i))
        .sort((a, b) => b - a);
      indices.forEach(onRemoveForeignKey);
      if (indices.length > 0) {
        message.success(`已移除 ${indices.length} 个外键`);
      }
    },
    [currentDraft, canEdit, onRemoveForeignKey]
  );

  if (!currentDraft) {
    return <Empty description="请选择左侧草稿后查看 ER 图" style={{ marginTop: 120 }} />;
  }

  if (visibleDrafts.length === 0) {
    return <Empty description="当前数据源下暂无草稿" style={{ marginTop: 120 }} />;
  }

  return (
    <div style={{ width: "100%", height: "calc(100vh - 280px)", minHeight: 480, background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onEdgesDelete={onEdgesDelete}
        nodeTypes={nodeTypes}
        nodesConnectable={canEdit}
        edgesFocusable
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} color="#e8e8e8" />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(n) =>
            (n.data as TableNodeData | undefined)?.isCurrent ? "#1677ff" : "#d9d9d9"
          }
          maskColor="rgba(0,0,0,0.05)"
        />
      </ReactFlow>
      <div style={{ padding: "8px 12px", fontSize: 12, color: "#666" }}>
        <Text type="secondary">
          拖拽「编辑中」表节点的字段（右侧 handle）到其他表字段（左侧 handle）创建外键；
          选中连线后按 <Text keyboard>Delete</Text> 移除。
        </Text>
      </div>
    </div>
  );
};

const ERGraph = (props: ERGraphProps) => (
  <ReactFlowProvider>
    <ERGraphInner {...props} />
  </ReactFlowProvider>
);

export default ERGraph;
