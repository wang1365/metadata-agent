import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Checkbox, Drawer, Select, Space, Table, Tabs, Tag, Typography } from 'antd';
import { api, send } from './api';
import ForceGraphCanvas from './ForceGraphCanvas';
import { bundleRelations, type EntityGraphData, type EntityNode, type ForceMemory, type RelationBundle } from './forceGraphData';
import type { MetaTable, Relation } from './types';

const origins: Record<string, string> = { constraint: '数据库约束', dictionary: '字典声明', inferred: '推断关系' };
const reviews: Record<string, string> = { pending: '待确认', confirmed: '已确认', rejected: '已驳回' };
const confidences: Record<string, string> = { high: '高', medium: '中', low: '低' };
const popupContainer = () => (document.fullscreenElement as HTMLElement) ?? document.body;

export default function EntityGraph({ snapshotId, tableOptions, memory, onEvidence }: {
  snapshotId: string; tableOptions: { value: string; label: string }[]; memory: ForceMemory;
  onEvidence: (ids: string[]) => void;
}) {
  const [origin, setOrigin] = useState(['constraint', 'dictionary', 'inferred']);
  const [confidence, setConfidence] = useState(['high', 'medium', 'low']);
  const [review, setReview] = useState(['pending', 'confirmed']);
  const [root, setRoot] = useState<string>();
  const [hideIsolated, setHideIsolated] = useState(false);
  const [selected, setSelected] = useState<string>();
  const [focus, setFocus] = useState<{ id: string }>();
  const [nodeOpen, setNodeOpen] = useState(false);
  const [selectedBundle, setSelectedBundle] = useState<string>();
  const [reviewBusy, setReviewBusy] = useState(false);
  const cache = useQueryClient();
  const { message } = App.useApp();
  const graph = useQuery({ queryKey: ['entity-graph', snapshotId, root, origin, confidence, review], queryFn: ({ signal }) => {
    const params = new URLSearchParams();
    for (const [key, values] of [['origin', origin], ['confidence', confidence], ['review', review]] as const) {
      for (const value of values.length ? values : ['']) params.append(key, value);
    }
    if (root) params.set('root', root);
    return api<EntityGraphData>(`/snapshots/${snapshotId}/entity-graph?${params}`, { signal });
  } });
  const bundles = useMemo(() => bundleRelations(graph.data?.relationships ?? []), [graph.data]);
  const nodes = useMemo(() => {
    const all = graph.data?.nodes ?? [];
    if (!hideIsolated) return all;
    const connected = new Set(bundles.flatMap(bundle => [bundle.source, bundle.target]));
    return all.filter(node => connected.has(node.id) || node.id === selected || node.id === root);
  }, [graph.data, bundles, hideIsolated, selected, root]);
  const bundle = bundles.find(item => item.id === selectedBundle);
  const node = useQuery({ queryKey: ['entity-detail', snapshotId, selected], enabled: !!selected && nodeOpen,
    queryFn: ({ signal }) => api<MetaTable>(`/snapshots/${snapshotId}/tables/${encodeURIComponent(selected!)}`, { signal }) });
  function openNode(id: string) { setSelected(id); setNodeOpen(true); }
  function locateNode(id?: string) { setSelected(id); setNodeOpen(false); setFocus(id ? { id } : undefined); }
  async function audit(relation: Relation, status: string) {
    if (!relation.id || reviewBusy) return;
    setReviewBusy(true);
    try {
      await api(`/relationships/${encodeURIComponent(relation.id)}?snapshot_id=${encodeURIComponent(snapshotId)}`, send('PATCH', { status, note: '实体关系力导图审核' }));
      await Promise.all([cache.invalidateQueries({ queryKey: ['entity-graph', snapshotId] }), cache.invalidateQueries({ queryKey: ['graph', snapshotId] })]);
    } catch (error) { void message.error(error instanceof Error ? error.message : String(error)); }
    finally { setReviewBusy(false); }
  }
  const relationColumns = [
    { title: '来源字段', render: (_: unknown, relation: Relation) => `${relation.source_table}.${relation.source_columns?.join(', ')}` },
    { title: '目标字段', render: (_: unknown, relation: Relation) => `${relation.target_table}.${relation.target_columns?.join(', ')}` },
    { title: '置信度', render: (_: unknown, relation: Relation) => confidences[relation.confidence ?? 'medium'] },
    { title: '说明', dataIndex: 'explanation' },
    { title: '证据与审核', render: (_: unknown, relation: Relation) => <Space wrap><Button size="small" onClick={() => onEvidence(relation.evidence_ids)}>查看证据 ({relation.evidence_ids.length})</Button>{relation.id && <Select aria-label={`审核关系 ${relation.id}`} getPopupContainer={popupContainer} disabled={reviewBusy} value={relation.review ?? 'pending'} style={{width:110}} onChange={status => void audit(relation, status)} options={Object.entries(reviews).map(([value,label])=>({value,label}))}/>}</Space> },
  ];
  return <div className="entity-graph">
    <div className="entity-filters">
      <label>定位实体<Select getPopupContainer={popupContainer} aria-label="搜索定位实体" showSearch allowClear placeholder="按名称搜索" value={selected} onChange={locateNode} options={(graph.data?.nodes ?? []).map(node=>({value:node.id,label:node.name}))}/></label>
      <label>中心表<Select getPopupContainer={popupContainer} aria-label="力导图中心表" showSearch allowClear placeholder="全部表 / 选择后一跳" value={root} onChange={setRoot} options={tableOptions}/></label>
      <label>关系来源<Select getPopupContainer={popupContainer} aria-label="力导图关系来源" mode="multiple" maxTagCount="responsive" value={origin} onChange={setOrigin} options={Object.entries(origins).map(([value,label])=>({value,label}))}/></label>
      <label>置信度<Select getPopupContainer={popupContainer} aria-label="力导图置信度" mode="multiple" maxTagCount="responsive" value={confidence} onChange={setConfidence} options={Object.entries(confidences).map(([value,label])=>({value,label}))}/></label>
      <label>审核状态<Select getPopupContainer={popupContainer} aria-label="力导图审核状态" mode="multiple" maxTagCount="responsive" value={review} onChange={setReview} options={Object.entries(reviews).map(([value,label])=>({value,label}))}/></label>
    </div>
    <Space wrap className="section"><Checkbox checked={!hideIsolated} onChange={event=>setHideIsolated(!event.target.checked)}>显示孤立实体</Checkbox><Tag color="blue">蓝色实线：数据库约束</Tag><Tag color="purple">紫色点线：字典声明</Tag><Tag color="gold">棕色虚线：推断关系</Tag><Typography.Text type="secondary">圆形：表 · 菱形：视图 · 双边框：拖动固定</Typography.Text></Space>
    <Typography.Paragraph type="secondary" role="status">{graph.isFetching ? '正在读取快照关联…' : `${nodes.length}/${graph.data?.nodes.length ?? 0} 个实体 · ${graph.data?.returned_relationships ?? 0}/${graph.data?.total_relationships ?? 0} 条原始关系 · ${bundles.length} 条展示连线`}</Typography.Paragraph>
    {graph.error && <Alert type="error" showIcon message={graph.error.message}/>}
    {graph.data?.truncated && <Alert type="warning" showIcon message={`当前仅加载 ${graph.data.returned_relationships}/${graph.data.total_relationships} 条原始关系，整体关联可能不完整；请选择中心表或筛选后查看。所有范围内实体仍保留，孤立表示当前已加载关系中没有连线。`}/>}
    <ForceGraphCanvas nodes={nodes} bundles={bundles} memory={memory} selected={selected} focus={focus} onSelect={id => {setSelected(id);setNodeOpen(false);}} onNode={openNode} onBundle={setSelectedBundle}/>
    <Tabs className="section force-lists" items={[
      {key:'entities',label:'实体列表',children:<Table<EntityNode> size="small" rowKey="id" dataSource={nodes} columns={[{title:'名称',dataIndex:'name'},{title:'类型',dataIndex:'kind',render:value=>value==='view'?'视图':'表'},{title:'说明',dataIndex:'comment'},{title:'字段数',dataIndex:'column_count'},{title:'操作',render:(_, item)=><Space><Button onClick={()=>locateNode(item.id)}>定位</Button><Button onClick={()=>openNode(item.id)}>字段与证据</Button></Space>}]} pagination={{pageSize:20,showSizeChanger:false}}/>},
      {key:'relations',label:'关联列表',children:<Table<RelationBundle> size="small" rowKey="id" dataSource={bundles} columns={[{title:'来源实体',dataIndex:'source'},{title:'目标实体',dataIndex:'target'},{title:'来源',dataIndex:'origin',render:value=>origins[value]},{title:'审核',dataIndex:'review',render:value=>reviews[value]},{title:'原始关系数',render:(_,item)=>item.relations.length},{title:'详情',render:(_,item)=><Button onClick={()=>setSelectedBundle(item.id)}>查看与审核</Button>}]} pagination={{pageSize:20,showSizeChanger:false}}/>},
    ]}/>
    <Drawer getContainer={popupContainer} width="min(1000px,96vw)" title={'实体详情 · '+(selected??'')} open={nodeOpen} onClose={()=>setNodeOpen(false)}>
      {node.error && <Alert type="error" message={node.error.message}/>}
      <Typography.Paragraph>{node.data?.comment}</Typography.Paragraph>
      {node.data && <Button onClick={()=>onEvidence(node.data!.evidence_ids)}>查看实体依据 ({node.data.evidence_ids.length})</Button>}
      <Table className="section" size="small" rowKey="name" loading={node.isFetching} dataSource={node.data?.columns ?? []} columns={[{title:'字段',dataIndex:'name'},{title:'类型',dataIndex:'type'},{title:'说明',dataIndex:'comment'}]}/>
    </Drawer>
    <Drawer getContainer={popupContainer} width="min(1250px,96vw)" title={bundle ? `${bundle.source} → ${bundle.target} · ${origins[bundle.origin]} · ${bundle.relations.length}条原始关系` : '关联详情'} open={!!bundle} onClose={()=>setSelectedBundle(undefined)}>
      <Alert type="info" showIcon message="合并仅用于展示，下面每条关系保留独立字段、说明、证据和审核状态；箭头不表示加工血缘。"/>
      <Table<Relation> className="section" size="small" rowKey={(relation,index)=>relation.id || String(index)} dataSource={bundle?.relations ?? []} columns={relationColumns} scroll={{x:900}} expandable={{expandedRowRender:relation=><pre className="evidence">{JSON.stringify(relation,null,2)}</pre>}}/>
    </Drawer>
  </div>;
}
