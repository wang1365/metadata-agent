import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Input, Select, Space, Switch, Tag, Typography } from 'antd';
import { api } from './api';
import { formatTime } from './time';
import type { Job } from './types';

interface TaskEvent { seq: number; time: string; execution_id?: string; type: string; level: string; message: string; tool?: string; duration_ms?: number; code?: string; }
interface LogPage { events: TaskEvent[]; next_cursor: number; has_more: boolean; truncated: boolean; status: string; }

export default function TaskLogs({ job, onClose }: { job: Job; onClose: () => void }) {
  const cursor = useRef(0);
  const end = useRef<HTMLDivElement>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [level, setLevel] = useState('all');
  const [search, setSearch] = useState('');
  const [follow, setFollow] = useState(true);
  const [truncated, setTruncated] = useState(false);
  const logs = useQuery({
    queryKey: ['task-logs', job.id],
    queryFn: async () => {
      const page = await api<LogPage>(`/analyses/${job.id}/logs?after=${cursor.current}&limit=500`);
      cursor.current = page.next_cursor;
      return page;
    },
    refetchInterval: query => ['queued', 'running'].includes(job.status) || query.state.data?.has_more ? 2000 : false,
  });
  const { refetch } = logs;
  useEffect(() => {
    void refetch();
    if (['queued', 'running'].includes(job.status)) return;
    // Allow the final event commit/relay to catch up with the terminal status.
    const timer = setTimeout(() => { void refetch(); }, 2200);
    return () => clearTimeout(timer);
  }, [job.status, refetch]);
  useEffect(() => {
    if (!logs.data) return;
    const page = logs.data;
    setTruncated(previous => previous || page.truncated);
    setEvents(previous => {
      const merged = new Map(previous.map(event => [event.seq, event]));
      page.events.forEach(event => merged.set(event.seq, event));
      return [...merged.values()].sort((a, b) => a.seq - b.seq).slice(-2000);
    });
  }, [logs.data]);
  useEffect(() => { if (follow) end.current?.scrollIntoView({ block: 'nearest' }); }, [events, follow]);
  const visible = events.filter(event => (level === 'all' || event.level === level) && `${event.message} ${event.tool ?? ''} ${event.type} ${event.code ?? ''}`.toLowerCase().includes(search.toLowerCase()));
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `task-${job.id}-logs.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Drawer title="任务过程日志" open onClose={onClose} width="min(960px, 96vw)">
    <Typography.Paragraph className="mono">任务 {job.id}</Typography.Paragraph>
    <Space wrap><Tag color={job.status === 'running' ? 'blue' : undefined}>{job.stage}</Tag><span>查询 {job.queries}/30 · 样本 {job.sample_rows}/200</span><Button loading={logs.isFetching} onClick={() => void logs.refetch()}>刷新</Button><Button disabled={!events.length} onClick={download}>下载当前日志</Button></Space>
    <Typography.Paragraph type="secondary">运行中每 2 秒更新；每 15 秒记录进程心跳。日志展示执行事件摘要，模型上下文、推理内容、凭据和原始样本不进入此面板。</Typography.Paragraph>
    <Space wrap className="section"><Select aria-label="日志级别" value={level} onChange={setLevel} options={[{value:'all',label:'全部级别'},{value:'info',label:'信息'},{value:'warning',label:'警告'},{value:'error',label:'错误'}]}/><Input aria-label="筛选过程日志" placeholder="筛选工具、事件或错误" value={search} onChange={event => setSearch(event.target.value)}/><Switch checked={follow} onChange={setFollow} checkedChildren="跟随最新" unCheckedChildren="暂停滚动"/></Space>
    {logs.isError && <Alert type="error" showIcon message="日志读取失败" description={logs.error.message}/>}
    {(truncated || events.length >= 2000) && <Alert type="warning" message="日志窗口有上限：界面保留最近 2000 条，服务每任务保留最近 5000 条。下载内容为当前窗口。"/>}
    <div className="task-log-list" role="log" aria-label="任务执行事件" aria-live="polite">
      {!visible.length ? <Empty description={logs.isLoading ? '正在读取日志' : '暂无匹配日志；旧版已结束任务无法补录历史事件'}/> : visible.map(event => <div className={`task-log-row task-log-${event.level}`} key={event.seq}>
        <div><time dateTime={event.time}>{formatTime(event.time)}</time><Tag color={event.level === 'error' ? 'red' : event.level === 'warning' ? 'gold' : 'blue'}>{event.level}</Tag><code>{event.type}</code>{event.execution_id && <span className="muted"> 执行 {event.execution_id.slice(0,8)}</span>}</div>
        <div>{event.message} {event.tool && <strong className="mono">{event.tool}</strong>} {event.duration_ms !== undefined && <span className="muted"> · {(event.duration_ms/1000).toFixed(2)}s</span>} {event.code && <Tag>{event.code}</Tag>}</div>
      </div>)}<div ref={end}/>
    </div>
  </Drawer>;
}
