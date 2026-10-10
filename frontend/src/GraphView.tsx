import { useEffect, useRef, useState } from 'react';
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react';
import ELK from 'elkjs/lib/elk.bundled.js';
import { Alert, Button, Empty, Space, Tag } from 'antd';
import { toPng, toSvg } from 'html-to-image';
import type { Graph, MetaTable, Relation } from './types';
import '@xyflow/react/dist/style.css';

type TableNode = Node<{ table: MetaTable }>;
function TableCard({ data }: NodeProps<TableNode>) {
  return <div className="table-node"><Handle type="target" position={Position.Left}/><div className="table-node-title">{data.table.name}<span>{data.table.kind === 'view' ? '视图' : '表'}</span></div>{data.table.comment && <div className="table-node-comment">{data.table.comment}</div>}{data.table.columns.slice(0, 12).map(c => <div className="table-node-field" key={c.name}><span>{c.name}</span><code>{c.type}</code></div>)}{data.table.columns.length > 12 && <div className="table-node-comment">另有 {data.table.columns.length-12} 个字段，在目录中查看</div>}<Handle type="source" position={Position.Right}/></div>;
}
const nodeTypes = { table: TableCard };
const elk = new ELK();

export default function GraphView({ graph, onEdge }: { graph?: Graph; onEdge: (edge: Relation) => void }) {
  const [nodes, setNodes] = useState<TableNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [error, setError] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    if (!graph) { setNodes([]); setEdges([]); return; }
    const nextEdges: Edge[] = graph.edges.map((edge, index) => ({ id: String(index), source: edge.source_table, target: edge.target_table, label: edge.source_columns ? edge.source_columns.join(', ')+' → '+edge.target_columns?.join(', ') : [edge.source_column, edge.target_column].filter(Boolean).join(' → ') || '表级血缘', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: edge.origin === 'inferred' ? '#b45309' : '#2563eb', strokeDasharray: edge.origin === 'inferred' ? '6 4' : undefined }, data: { relation: edge } }));
    const children = graph.tables.map(table => ({ id: table.name, width: 290, height: 65+Math.min(table.columns.length,12)*27 }));
    elk.layout({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '55', 'elk.layered.spacing.nodeNodeBetweenLayers': '100' }, children, edges: nextEdges.map(e => ({ id: e.id, sources: [e.source], targets: [e.target] })) }).then(layout => {
      if (!active) return;
      setNodes(graph.tables.map(table => { const position = layout.children?.find(n => n.id === table.name); return { id: table.name, type: 'table', position: { x: position?.x ?? 0, y: position?.y ?? 0 }, data: { table } }; }));
      setEdges(nextEdges); setError('');
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [graph]);
  async function download(format: 'png' | 'svg') {
    if (!ref.current) return;
    try {
      const url = await (format === 'png' ? toPng : toSvg)(ref.current, { backgroundColor: '#f8fafc', filter: node => !((node as HTMLElement).classList?.contains('react-flow__controls')) });
      const link = document.createElement('a'); link.href = url; link.download = 'metadata-graph.'+format; link.click();
    } catch (e) { setError(String(e)); }
  }
  return <><div className="graph-toolbar"><Space><Tag color="blue">实线：声明 / 血缘</Tag><Tag color="gold">虚线：推断关联</Tag><span className="muted">当前对象的一跳关系 · 点击连线查看证据</span></Space><Space><Button onClick={() => void download('svg')}>导出 SVG</Button><Button onClick={() => void download('png')}>导出 PNG</Button></Space></div>{error && <Alert type="error" message={error}/>}<div className="graph-canvas" ref={ref}>{nodes.length ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView onEdgeClick={(_, edge) => onEdge(edge.data?.relation as Relation)}><Background/><Controls/><MiniMap pannable zoomable/></ReactFlow> : <Empty description="选择快照和表以查看关系图"/>}</div></>;
}
