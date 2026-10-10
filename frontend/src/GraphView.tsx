import { useEffect, useRef, useState } from 'react';
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, useNodesInitialized, useReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react';
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

function FitGraph({ nodes, fullscreen }: { nodes: TableNode[]; fullscreen: boolean }) {
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (!initialized || !nodes.length) return;
    const frame = requestAnimationFrame(() => { void fitView({ padding: 0.12, minZoom: 0.01, maxZoom: 1 }); });
    return () => cancelAnimationFrame(frame);
  }, [initialized, nodes, fullscreen, fitView]);
  return null;
}

export default function GraphView({ graph, onEdge }: { graph?: Graph; onEdge: (edge: Relation) => void }) {
  const [nodes, setNodes] = useState<TableNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [error, setError] = useState('');
  const [fullscreen, setFullscreen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const fullscreenRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function changed() { setFullscreen(document.fullscreenElement === fullscreenRef.current); }
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === fullscreenRef.current) await document.exitFullscreen();
      else await fullscreenRef.current?.requestFullscreen();
    } catch { setError('无法进入全屏，请检查浏览器是否允许全屏展示。'); }
  }
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
  return <div className="graph-view" ref={fullscreenRef}><div className="graph-toolbar"><Space><Tag color="blue">实线：声明 / 血缘</Tag><Tag color="gold">虚线：推断关联</Tag><span className="muted">{graph?.root ? '中心表一跳关系' : '当前快照全部表'} · {nodes.length} 个对象 · {edges.length} 条连线 · 点击连线查看证据</span></Space><Space><Button onClick={() => void toggleFullscreen()}>{fullscreen ? '退出全屏' : '全屏展示'}</Button><Button onClick={() => void download('svg')}>导出 SVG</Button><Button onClick={() => void download('png')}>导出 PNG</Button></Space></div>{graph?.edges_truncated && <Alert type="warning" showIcon message={`当前展示 ${edges.length}/${graph.total_edges} 条连线；表节点完整保留，可选择中心表缩小范围。`}/>}
    {error && <Alert type="error" message={error}/>}<div className="graph-canvas" ref={ref}>{nodes.length ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} minZoom={0.01} fitView fitViewOptions={{ minZoom: 0.01, maxZoom: 1, padding: 0.12 }} onEdgeClick={(_, edge) => onEdge(edge.data?.relation as Relation)}><FitGraph nodes={nodes} fullscreen={fullscreen}/><Background/><Controls/><MiniMap pannable zoomable/></ReactFlow> : <Empty description="当前快照没有表，请先选择已发布快照"/>}</div></div>;
}
