import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { api } from './api';

type DBObject = { name: string; kind: string };
type ObjectPage = { objects: DBObject[]; total: number; source_total: number };

export default function TablePicker({ sourceId, selected, onChange }: {
  sourceId: string; selected: string[]; onChange: (names: string[]) => void;
}) {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [match, setMatch] = useState('fuzzy');
  const [page, setPage] = useState(1);
  const [selecting, setSelecting] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const params = new URLSearchParams({ query, match });
  const path = `/sources/${sourceId}/objects?${params}`;
  const objects = useQuery({
    queryKey: ['source-objects', sourceId, query, match, page],
    queryFn: () => api<ObjectPage>(`${path}&offset=${(page - 1) * 50}&limit=50`),
    retry: false,
  });

  function changeSelection(names: string[]) {
    if (names.length > 500) { setSelectionError('每次最多选择 500 张表，请缩小范围。'); return; }
    setSelectionError('');
    onChange(names);
  }

  async function selectMatches() {
    setSelecting(true);
    setSelectionError('');
    try {
      const result = await api<ObjectPage>(`${path}&limit=500`);
      if (result.total > 500) { setSelectionError('筛选结果超过 500 张，请缩小范围后再全选。'); return; }
      changeSelection([...new Set([...selected, ...result.objects.map(item => item.name)])]);
    } catch (error) { setSelectionError(error instanceof Error ? error.message : String(error)); }
    finally { setSelecting(false); }
  }

  return <Space direction="vertical" style={{ width: '100%' }} size="middle">
    <Typography.Text>分析范围：先筛选表名，再勾选需要处理的表或视图。</Typography.Text>
    <Space.Compact style={{ width: '100%' }}>
      <Select disabled={selecting} aria-label="表名匹配方式" value={match} style={{ width: 125 }} onChange={value => { setMatch(value); setPage(1); }} options={[{ value: 'fuzzy', label: '模糊匹配' }, { value: 'regex', label: '正则表达式' }]}/>
      <Input.Search disabled={selecting} aria-label="筛选数据库表名" value={input} maxLength={256} allowClear enterButton="筛选" placeholder={match === 'regex' ? '例如 ^(sys_|biz_).* 或 _detail$' : '输入表名片段，例如 order'} onChange={event => setInput(event.target.value)} onSearch={value => { setQuery(value); setPage(1); }}/>
    </Space.Compact>
    <Typography.Text type="secondary">不区分大小写；模糊匹配按表名包含片段筛选，正则无需 / 分隔符。修改文字后点击“筛选”。</Typography.Text>
    {objects.error && <Alert type="error" showIcon message="表列表加载或筛选失败" description={objects.error.message}/>}
    {selectionError && <Alert type="error" showIcon message={selectionError}/>}
    <Space wrap>
      <Tag color="blue" aria-live="polite">已选 {selected.length} / 500 张</Tag>
      <Typography.Text type="secondary">匹配 {objects.data?.total ?? '—'} / 全库 {objects.data?.source_total ?? '—'} 张</Typography.Text>
      <Button loading={selecting} disabled={!objects.data?.total || objects.isFetching || objects.data.total > 500} onClick={() => void selectMatches()}>勾选全部匹配项</Button>
      <Button disabled={!selected.length || selecting} onClick={() => changeSelection([])}>清空选择</Button>
      <Button disabled={selecting} onClick={() => void objects.refetch()}>刷新表列表</Button>
    </Space>
    {(objects.data?.total ?? 0) > 500 && <Alert type="info" message="匹配项超过 500 张，可逐页勾选；批量全选前请缩小筛选范围。"/>}
    <Table<DBObject> size="small" rowKey="name" loading={objects.isFetching} dataSource={objects.data?.objects ?? []}
      locale={{ emptyText: '没有匹配的表，请调整筛选条件' }}
      rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: keys => changeSelection(keys.map(String)), getCheckboxProps: item => ({ disabled: selecting || (selected.length >= 500 && !selected.includes(item.name)) }) }}
      columns={[{ title: '表名', dataIndex: 'name', render: name => <span style={{ overflowWrap: 'anywhere' }}>{name}</span> }, { title: '类型', dataIndex: 'kind', width: 80, render: kind => kind === 'view' ? '视图' : '表' }]}
      pagination={{ current: page, pageSize: 50, total: objects.data?.total ?? 0, showSizeChanger: false, onChange: setPage }} scroll={{ y: 280 }}/>
    <Typography.Text type="secondary">跨页和切换筛选条件会保留勾选。未选择不能提交；范围外的关联和血缘只报告边界，不自动扩大分析范围。</Typography.Text>
    {!!selected.length && <Select aria-label="查看和移除已选表" mode="multiple" maxTagCount="responsive" style={{ width: '100%' }} value={selected} onChange={changeSelection} options={selected.map(name => ({ value: name, label: name }))} disabled={selecting}/>}
  </Space>;
}
