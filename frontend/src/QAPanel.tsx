import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Empty, Input, Space, Switch, Table, Tabs, Tag, Typography } from 'antd';
import { api, send } from './api';
import { formatTime } from './time';
import type { Answer, Job } from './types';

interface ConversationInfo { id: string; snapshot_id: string; title: string; updated: string; status: string; task_count: number; }
const statuses: Record<string, string> = { queued: '排队中', running: '回答中', succeeded: '已完成', failed: '失败', cancelled: '已取消', partial_success: '部分完成' };

export default function QAPanel({ snapshot, conversationId, onOpen, onEvidence, onLogs }: {
  snapshot?: string; conversationId?: string; onOpen: (snapshot: string, conversation?: string) => void;
  onEvidence: (ids: string[]) => void; onLogs: (job: Job) => void;
}) {
  const [tab, setTab] = useState('chat');
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [currentOnly, setCurrentOnly] = useState(true);
  const [page, setPage] = useState(1);
  const cache = useQueryClient();
  const { message } = App.useApp();
  const conversation = useQuery({ queryKey: ['conversation', conversationId], queryFn: () => api<{ answers: Answer[]; tasks: Job[] }>('/conversations/' + conversationId), enabled: !!conversationId, refetchInterval: 2000 });
  const history = useQuery({ queryKey: ['qa-history', currentOnly ? snapshot : undefined, page], queryFn: () => api<{ conversations: ConversationInfo[]; total: number }>(`/conversations?offset=${(page - 1) * 20}&limit=20${currentOnly && snapshot ? '&snapshot_id=' + encodeURIComponent(snapshot) : ''}`), refetchInterval: tab === 'history' ? 2000 : false, enabled: tab === 'history' });

  async function submit() {
    const text = question.trim();
    if (!snapshot || !text || sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    try {
      const result = await api<{ conversation_id: string }>(`/snapshots/${snapshot}/questions`, send('POST', { question: text, conversation_id: conversationId }));
      onOpen(snapshot, result.conversation_id);
      setQuestion('');
      await cache.invalidateQueries();
    } catch (error) { void message.error(error instanceof Error ? error.message : String(error)); }
    finally { sendingRef.current = false; setSending(false); }
  }

  return <Tabs activeKey={tab} onChange={setTab} items={[
    { key: 'chat', label: '当前问答', children: <>
      <Space className="section" wrap><Alert type="info" showIcon message="基于已发布快照回答，问答独立排队；同一会话按提问顺序执行。"/><Button disabled={!snapshot || sending} onClick={() => { if (snapshot) onOpen(snapshot); setQuestion(''); }}>新建会话</Button></Space>
      {conversation.error && <Alert type="error" message={conversation.error.message}/>}
      <div className="chat">{conversation.data?.tasks.map(job => {
        const answer = conversation.data?.answers.find(item => item.id === job.answer_id);
        return <div className="chat-turn" key={job.id}><div className="question">{job.question}</div><Space wrap><Typography.Text type="secondary">{formatTime(job.created)} 北京时间</Typography.Text><Tag color={job.status === 'failed' ? 'red' : job.status === 'succeeded' ? 'green' : 'blue'}>{statuses[job.status] ?? job.status}</Tag><Button size="small" onClick={() => onLogs(job)}>过程日志</Button></Space>{answer ? <><div className="answer">{answer.answer}</div><Button size="small" onClick={() => onEvidence(answer.evidence_ids)}>查看引用依据 ({answer.evidence_ids.length})</Button></> : <Typography.Paragraph type={job.status === 'failed' ? 'danger' : 'secondary'}>{job.error ?? job.stage}</Typography.Paragraph>}</div>;
      })}{!conversationId && <Empty description="输入问题开始新会话，或从历史会话继续提问"/>}</div>
      <Input.TextArea aria-label="元数据问题" rows={3} value={question} disabled={sending} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void submit(); } }} maxLength={4000} placeholder="Enter 发送，Shift+Enter 换行"/>
      <div className="chat-actions"><Typography.Text type="secondary">Enter 发送 · Shift+Enter 换行　</Typography.Text><Button type="primary" disabled={!snapshot || !question.trim()} loading={sending} onClick={() => void submit()}>发送问题</Button></div>
    </> },
    { key: 'history', label: '历史会话', children: <>
      <Space className="section"><Switch disabled={!snapshot} checked={currentOnly && !!snapshot} onChange={value => { setCurrentOnly(value); setPage(1); }}/><span>仅当前快照</span><Button onClick={() => void history.refetch()}>刷新历史</Button></Space>
      {history.error && <Alert type="error" message={history.error.message}/>}
      <Table<ConversationInfo> rowKey="id" dataSource={history.data?.conversations ?? []} loading={history.isFetching} locale={{ emptyText: '暂无问答历史' }} columns={[{ title: '会话', dataIndex: 'title' }, { title: '提问/尝试次数', dataIndex: 'task_count', width: 130 }, { title: '最近提问（北京时间）', dataIndex: 'updated', render: formatTime, width: 210 }, { title: '状态', dataIndex: 'status', render: status => statuses[status] ?? status }, { title: '操作', render: (_, info) => <Button onClick={() => { onOpen(info.snapshot_id, info.id); setTab('chat'); setQuestion(''); }}>打开并继续</Button> }]} pagination={{ current: page, pageSize: 20, total: history.data?.total ?? 0, showSizeChanger: false, onChange: setPage }}/>
    </> },
  ]}/>;
}
