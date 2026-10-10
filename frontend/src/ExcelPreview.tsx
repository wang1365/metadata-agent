import { useState, type CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Input, Select, Space, Spin, Typography } from 'antd';
import { api } from './api';
import type { Source } from './types';

interface SheetPreview {
  columns: { index: number; label: string; width: number; hidden: boolean }[];
  rows: { index: number; height: number; hidden: boolean }[];
  cells: { row: number; column: number; value: string; rowSpan: number; colSpan: number; style: CSSProperties }[];
  warnings: string[];
}
function initialRegion(sheet?: { rows: number; columns: number }) {
  const columns = Math.max(1, Math.min(40, sheet?.columns ?? 1));
  let label = '', index = columns;
  while (index) { index--; label = String.fromCharCode(65 + index % 26) + label; index = Math.floor(index / 26); }
  return `A1:${label}${Math.max(1, Math.min(sheet?.rows ?? 1, 100, Math.floor(2000 / columns)))}`;
}

export default function ExcelPreview({ source }: { source: Source }) {
  const [sheet, setSheet] = useState(source.sheets?.[0]?.name ?? '');
  const [region, setRegion] = useState(initialRegion(source.sheets?.[0]));
  const [applied, setApplied] = useState(region);
  const preview = useQuery({ queryKey: ['excel-presentation', source.id, sheet, applied], enabled: !!sheet,
    queryFn: () => api<SheetPreview>(`/sources/${source.id}/preview?formatted=true&name=${encodeURIComponent(sheet)}&region=${encodeURIComponent(applied)}`) });
  const data = preview.data;
  const rows = new Map<number, SheetPreview['cells']>();
  for (const cell of data?.cells ?? []) { const list = rows.get(cell.row) ?? []; list.push(cell); rows.set(cell.row, list); }
  return <Space direction="vertical" className="section" style={{ width: '100%' }} size="middle">
    <Typography.Text type="secondary">原工作表预览 · 保留行列、空白区域、合并单元格及常见样式，不执行智能体分析。</Typography.Text>
    <Select aria-label="选择工作表" style={{ width: '100%' }} value={sheet || undefined} onChange={name => {
      setSheet(name); const next = initialRegion(source.sheets?.find(item => item.name === name)); setRegion(next); setApplied(next);
    }} options={source.sheets?.map(item => ({ value: item.name, label: `${item.name} · ${item.rows}行 × ${item.columns}列` }))}/>
    <Input.Search aria-label="预览单元格区域" value={region} onChange={event => setRegion(event.target.value)} onSearch={setApplied} enterButton="读取区域" placeholder="例如 A1:G65，每次最多2000单元格"/>
    {preview.error && <Alert type="error" showIcon message={preview.error.message}/>}
    <Spin spinning={preview.isFetching}><div className="excel-sheet" tabIndex={0} role="region" aria-label={`${sheet} 工作表，可横向和纵向滚动`}>
      <table className="excel-grid" aria-label={sheet} style={{width:48+(data?.columns.reduce((sum,column)=>sum+(column.hidden?0:column.width),0) ?? 0)}}><colgroup><col style={{ width: 48 }}/>{data?.columns.map(column => <col key={column.index} style={{ width: column.hidden ? 0 : column.width, visibility: column.hidden ? 'collapse' : undefined }}/>)}</colgroup>
        <thead><tr><th className="excel-corner"/>{data?.columns.map(column => <th key={column.index} style={{ display: column.hidden ? 'none' : undefined }} scope="col">{column.label}</th>)}</tr></thead>
        <tbody>{data?.rows.map(row => <tr key={row.index} style={{ height: row.height, display: row.hidden ? 'none' : undefined }}><th scope="row">{row.index}</th>{rows.get(row.index)?.map(cell => <td key={cell.column} rowSpan={cell.rowSpan} colSpan={cell.colSpan} style={{ ...cell.style, display: data.columns.find(column => column.index === cell.column)?.hidden ? 'none' : undefined }} title={cell.value}><div className="excel-cell-value">{cell.value}</div></td>)}</tr>)}</tbody>
      </table>
    </div></Spin>
    {data?.warnings.map((warning, i) => <Typography.Text type="secondary" key={i}>{warning}</Typography.Text>)}
  </Space>;
}
