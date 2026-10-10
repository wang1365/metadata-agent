import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Drawer, Empty, Input, InputNumber, Select, Space, Table, Tabs, Typography } from 'antd';
import { api, send } from './api';
import { formatTime } from './time';
import type { MetaTable, Source } from './types';

export default function SourceDetails({ source, onClose }: { source: Source; onClose: () => void }) {
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [region, setRegion] = useState('A1:J20');
  const [appliedRegion, setAppliedRegion] = useState('A1:J20');
  const [columns, setColumns] = useState<string[]>([]);
  const [limit, setLimit] = useState(10);
  const [rows, setRows] = useState<Record<string, unknown>[]>();
  const [sampleBusy, setSampleBusy] = useState(false);
  const [sampleError, setSampleError] = useState('');
  const details = useQuery({ queryKey: ['source-details', source.id], queryFn: () => api<Source>('/sources/' + source.id) });
  const info = details.data ?? source;
  const objects = useQuery({ queryKey: ['source-preview-objects', source.id, query, page], queryFn: () => api<{objects: {name: string; kind: string}[]; total: number}>(`/sources/${source.id}/objects?query=${encodeURIComponent(query)}&offset=${(page - 1) * 50}&limit=50`), enabled: source.type === 'mysql' });
  const structure = useQuery({ queryKey: ['source-structure', source.id, name], queryFn: () => api<{tables: MetaTable[]; foreign_keys: unknown[]; warnings: string[]}>(`/sources/${source.id}/preview?name=${encodeURIComponent(name)}`), enabled: source.type === 'mysql' && !!name });
  const cells = useQuery({ queryKey: ['source-cells', source.id, name, appliedRegion], queryFn: () => api<{cells: {row: number; column: number; value: string}[]}>(`/sources/${source.id}/preview?name=${encodeURIComponent(name)}&region=${encodeURIComponent(appliedRegion)}`), enabled: source.type === 'excel' && !!name });
  const table = structure.data?.tables[0];
  const error = details.error || objects.error || structure.error || cells.error;

  function choose(name: string) { setName(name); setColumns([]); setRows(undefined); setSampleError(''); }
  async function sample() {
    if (!name || !columns.length || sampleBusy) return;
    setSampleBusy(true); setSampleError(''); setRows(undefined);
    try { const result = await api<{rows: Record<string, unknown>[]}>(`/sources/${source.id}/sample`, send('POST', {table: name, columns, limit})); setRows(result.rows); }
    catch (error) { setSampleError(error instanceof Error ? error.message : String(error)); }
    finally { setSampleBusy(false); }
  }

  return <Drawer title={'数据源详情 · ' + info.name} width="min(1100px, 96vw)" open onClose={onClose}>
    {error && <Alert type="error" showIcon message={error.message}/>}
    <Descriptions bordered column={2} size="small" items={[{key:'type',label:'类型',children:info.type === 'mysql' ? 'MySQL' : 'Excel'}, {key:'time',label:'创建时间（北京时间）',children:formatTime(info.created)}, ...(info.config ? [{key:'host',label:'地址',children:`${info.config.host}:${info.config.port}`},{key:'database',label:'数据库',children:info.config.database},{key:'username',label:'用户',children:info.config.username}] : [{key:'size',label:'文件大小',children:`${((info.size ?? 0)/1024).toFixed(1)} KB`},{key:'sheets',label:'Sheet 数',children:info.sheets?.length}])]}/>
    {info.type === 'mysql' ? <>
      <Input.Search className="section" aria-label="筛选预览表" placeholder="按表名片段筛选" allowClear onSearch={value => {setQuery(value); setPage(1);}}/>
      <Table size="small" rowKey="name" loading={objects.isFetching} dataSource={objects.data?.objects ?? []} columns={[{title:'表/视图',dataIndex:'name'},{title:'类型',dataIndex:'kind'},{title:'操作',render:(_, row)=><Button disabled={sampleBusy} type={row.name === name ? 'primary' : 'default'} onClick={()=>choose(row.name)}>预览结构</Button>}]} pagination={{current:page,pageSize:50,total:objects.data?.total ?? 0,showSizeChanger:false,onChange:setPage}}/>
      {!name ? <Empty description="选择一个表或视图预览原始结构"/> : <><Typography.Title level={5}>{name}</Typography.Title><Tabs items={[
        {key:'structure',label:'数据结构',children:<><Table size="small" rowKey="name" loading={structure.isFetching} dataSource={table?.columns ?? []} columns={[{title:'字段',dataIndex:'name'},{title:'类型',dataIndex:'type'},{title:'允许空',dataIndex:'nullable',render:value=>value?'是':'否'},{title:'默认值',dataIndex:'default'},{title:'注释',dataIndex:'comment'}]}/><Typography.Title level={5}>声明的键与索引</Typography.Title><Table size="small" rowKey={row=>row.kind+':'+row.name} dataSource={table?.keys ?? []} columns={[{title:'名称',dataIndex:'name'},{title:'类型',dataIndex:'kind'},{title:'有序字段',dataIndex:'columns',render:columns=>columns.join(', ')}]}/>{structure.data?.warnings.map((warning,i)=><Alert key={i} type="warning" message={warning}/>)}</>},
        {key:'samples',label:'样例数据',children:<Space direction="vertical" style={{width:'100%'}} size="middle"><Alert type="info" showIcon message="点击后才读取：最多20行、20列，每值最多200字符；只用于浏览器预览，不发送给模型或保存到快照。读取首批行，不代表随机或全量数据。"/><Select aria-label="选择样例字段" mode="multiple" placeholder="选择需要预览的字段（最多20列）" maxCount={20} style={{width:'100%'}} disabled={sampleBusy} value={columns} onChange={value=>{setColumns(value);setRows(undefined);}} options={(table?.columns ?? []).map(column=>({value:column.name,label:column.name}))}/><Space><span>行数</span><InputNumber aria-label="样例行数" precision={0} min={1} max={20} disabled={sampleBusy} value={limit} onChange={value=>setLimit(value ?? 10)}/><Button loading={sampleBusy} disabled={!columns.length || structure.isFetching} onClick={()=>void sample()}>读取样例</Button></Space>{sampleError&&<Alert type="error" message={sampleError}/>}<Table size="small" rowKey={(_,index)=>String(index)} pagination={false} dataSource={rows ?? []} columns={columns.map(column=>({title:column,dataIndex:column,render:value=>value===null?'NULL':String(value ?? '')}))} scroll={{x:'max-content'}} locale={{emptyText:rows ? '没有样例行' : '尚未读取样例'}}/></Space>},
      ]}/></>}
    </> : <Space direction="vertical" className="section" style={{width:'100%'}} size="middle"><Typography.Text>查看原始 Sheet 与单元格，不执行智能体分析。</Typography.Text><Select aria-label="选择工作表" style={{width:'100%'}} placeholder="选择 Sheet" value={name || undefined} onChange={value=>{choose(value);const sheet=info.sheets?.find(sheet=>sheet.name===value);const area=`A1:${String.fromCharCode(64+Math.max(1,Math.min(10,sheet?.columns ?? 1)))}${Math.max(1,Math.min(20,sheet?.rows ?? 1))}`;setRegion(area);setAppliedRegion(area);}} options={info.sheets?.map(sheet=>({value:sheet.name,label:`${sheet.name} · ${sheet.rows}行 × ${sheet.columns}列`}))}/><Input.Search aria-label="预览单元格区域" value={region} onChange={event=>setRegion(event.target.value)} onSearch={setAppliedRegion} enterButton="读取区域" placeholder="例如 A1:J20，每次最多2000单元格"/><Table size="small" rowKey={cell=>`${cell.row}:${cell.column}`} loading={cells.isFetching} dataSource={cells.data?.cells ?? []} columns={[{title:'行',dataIndex:'row',width:70},{title:'列',dataIndex:'column',width:70},{title:'原始内容',dataIndex:'value'}]}/></Space>}
  </Drawer>;
}
