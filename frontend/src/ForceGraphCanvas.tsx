import { useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { Alert, Button, Empty, Space, Typography } from 'antd';
import { floatingPosition, gridPositions, type EntityNode, type ForceMemory, type RelationBundle } from './forceGraphData';
import type { LayoutResult } from './forceLayout';

const colors: Record<string, string> = { constraint: '#2563eb', dictionary: '#7c3aed', inferred: '#b45309' };
const styles: cytoscape.StylesheetJson = [
  { selector: 'node', style: { width: 36, height: 36, 'background-color': '#2563eb', label: 'data(label)', 'font-size': 11, color: '#334155', 'text-valign': 'bottom', 'text-margin-y': 7, 'text-max-width': '150px', 'text-wrap': 'ellipsis', 'border-width': 2, 'border-color': '#fff', 'min-zoomed-font-size': 7 } },
  { selector: 'node[kind = "view"]', style: { shape: 'diamond', 'background-color': '#0f766e' } },
  { selector: 'edge', style: { width: 1.4, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'arrow-scale': 0.7, label: 'data(label)', 'font-size': 10, 'text-background-color': '#fff', 'text-background-opacity': 0.8, 'min-zoomed-font-size': 8 } },
  { selector: 'edge[origin = "inferred"]', style: { 'line-style': 'dashed' } },
  { selector: 'edge[origin = "dictionary"]', style: { 'line-style': 'dotted' } },
  { selector: 'edge[review = "rejected"]', style: { 'line-color': '#94a3b8', 'target-arrow-color': '#94a3b8', 'line-style': 'dotted' } },
  { selector: '.faded', style: { opacity: 0.12 } },
  { selector: 'node.focused', style: { 'border-color': '#f59e0b', 'border-width': 4, 'min-zoomed-font-size': 0, 'z-index': 20 } },
  { selector: 'node.pinned', style: { 'border-style': 'double', 'border-width': 4 } },
  { selector: 'edge.focused', style: { width: 3, 'min-zoomed-font-size': 0, 'z-index': 10 } },
];

export default function ForceGraphCanvas({ nodes, bundles, memory, selected, focus, onSelect, onNode, onBundle }: {
  nodes: EntityNode[]; bundles: RelationBundle[]; memory: ForceMemory; selected?: string; focus?: { id: string };
  onSelect: (id: string) => void; onNode: (id: string) => void; onBundle: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const core = useRef<cytoscape.Core | undefined>(undefined);
  const worker = useRef<Worker | undefined>(undefined);
  const callbacks = useRef({ onSelect, onNode, onBundle });
  const hovered = useRef<string | undefined>(undefined);
  const graphVisible = useRef(false);
  const generation = useRef(0);
  const lastVersion = useRef(0);
  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState('等待图数据');
  const [warning, setWarning] = useState('');
  const [tooltip, setTooltip] = useState('');
  const [fullscreen, setFullscreen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [floating, setFloating] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => { callbacks.current = { onSelect, onNode, onBundle }; }, [onSelect, onNode, onBundle]);
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const changed = () => { if (preference.matches) setFloating(false); };
    preference.addEventListener('change', changed);
    return () => preference.removeEventListener('change', changed);
  }, []);

  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({ container: container.current, elements: [], style: styles, layout: { name: 'preset' },
      minZoom: 0.005, maxZoom: 5, wheelSensitivity: 0.2, pixelRatio: 1, hideEdgesOnViewport: true });
    core.current = cy;
    cy.on('tap', 'node', event => callbacks.current.onSelect(event.target.data('entityId')));
    cy.on('dbltap', 'node', event => callbacks.current.onNode(event.target.data('entityId')));
    cy.on('tap', 'edge', event => callbacks.current.onBundle(event.target.data('bundleId')));
    cy.on('mouseover', 'node', event => { hovered.current = event.target.id(); setTooltip(event.target.data('hint')); });
    cy.on('mouseout', 'node', () => { hovered.current = undefined; setTooltip(''); });
    cy.on('dragfree', 'node', event => {
      const id = event.target.data('entityId') as string;
      memory.positions.set(id, { ...event.target.position() }); memory.pinned.add(id);
      event.target.addClass('pinned');
    });
    const resize = new ResizeObserver(entries => {
      graphVisible.current = entries.some(entry => entry.contentRect.width > 0 && entry.contentRect.height > 0);
      cy.resize();
    });
    resize.observe(container.current);
    const fullscreenHost = panel.current?.closest('.entity-graph') ?? panel.current;
    const changed = () => {
      setFullscreen(document.fullscreenElement === fullscreenHost);
      requestAnimationFrame(() => { if (!cy.destroyed()) { cy.resize(); cy.fit(undefined, 40); } });
    };
    document.addEventListener('fullscreenchange', changed);
    return () => {
      resize.disconnect(); document.removeEventListener('fullscreenchange', changed);
      worker.current?.terminate(); cy.destroy(); core.current = undefined;
      if (document.fullscreenElement === fullscreenHost) void document.exitFullscreen().catch(() => {});
    };
  }, [memory]);

  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    const current = ++generation.current;
    let active = true;
    worker.current?.terminate(); worker.current = undefined;
    const needsLayout = !memory.settled || nodes.some(node => !memory.positions.has(node.id)) || version !== lastVersion.current;
    lastVersion.current = version;
    const grid = new Map(gridPositions(nodes.map(node => node.id)).map(position => [position.id, position]));
    for (const node of nodes) if (!memory.positions.has(node.id)) memory.positions.set(node.id, grid.get(node.id)!);
    const nodeIds = new Set(nodes.map(node => node.id));
    cy.batch(() => {
      cy.elements().remove();
      cy.add(nodes.map(node => ({ group: 'nodes' as const, data: { id: 'node:' + node.id, entityId: node.id, label: node.name, kind: node.kind, hint: `${node.name} · ${node.kind === 'view' ? '视图' : '表'} · ${node.column_count}字段${node.comment ? ' · ' + node.comment : ''}` }, position: memory.positions.get(node.id), classes: memory.pinned.has(node.id) ? 'pinned' : '' })));
      cy.add(bundles.filter(bundle => nodeIds.has(bundle.source) && nodeIds.has(bundle.target)).map(bundle => ({ group: 'edges' as const, data: { id: 'edge:' + bundle.id, bundleId: bundle.id, source: 'node:' + bundle.source, target: 'node:' + bundle.target, origin: bundle.origin, review: bundle.review, color: colors[bundle.origin] ?? '#64748b', label: bundle.relations.length > 1 ? `${bundle.relations.length}条关系` : '' } })));
    });
    cy.fit(undefined, 40);
    setWarning('');
    if (!nodes.length) { setStatus('当前范围没有实体'); return; }
    if (!needsLayout) { setStatus('保留已有布局 · 拖动可固定节点'); return; }
    memory.settled = false;
    setStatus('布局迭代 0/300');
    let timer: ReturnType<typeof setTimeout>;
    const failed = () => {
      if (!active || generation.current !== current) return;
      worker.current?.terminate(); clearTimeout(timer);
      setWarning('力导布局暂不可用，保留已有位置并使用网格放置新节点；可点击重新布局。');
      setStatus('布局回退完成'); memory.settled = true;
    };
    try {
      const layout = new Worker(new URL('./forceLayout.worker.ts', import.meta.url), { type: 'module' });
      worker.current = layout;
      timer = setTimeout(failed, 6500);
      layout.onmessage = (event: MessageEvent<LayoutResult & { generation: number; type: string }>) => {
        if (!active || event.data.generation !== current || generation.current !== current) return;
        if (event.data.type === 'progress') { setStatus(`布局迭代 ${event.data.iterations}/300`); return; }
        if (event.data.type !== 'done') { failed(); return; }
        clearTimeout(timer); layout.terminate(); worker.current = undefined;
        cy.batch(() => {
          for (const position of event.data.positions) {
            if (!memory.pinned.has(position.id)) memory.positions.set(position.id, { x: position.x, y: position.y });
            const node = cy.getElementById('node:' + position.id);
            if (node.length) node.position(memory.positions.get(position.id)!);
          }
        });
        memory.settled = true; cy.fit(undefined, 40);
        setStatus(`布局完成 ${event.data.iterations}/300 · ${event.data.duration_ms} ms`);
        if (event.data.timed_out) setWarning('达到5秒布局预算，使用当前布局；可缩小范围或重新布局。');
      };
      layout.onerror = failed;
      layout.postMessage({ generation: current, input: {
        nodes: nodes.map(node => { const position = memory.positions.get(node.id)!; return { id: node.id, ...position, ...(memory.pinned.has(node.id) ? { fx: position.x, fy: position.y } : {}) }; }),
        links: bundles.map(bundle => ({ source: bundle.source, target: bundle.target })),
      } });
    } catch { failed(); }
    return () => { active = false; clearTimeout(timer); worker.current?.terminate(); worker.current = undefined; };
  }, [nodes, bundles, memory, version]);

  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    let frame = 0, previous = 0;
    const restore = () => cy.batch(() => cy.nodes().forEach(node => {
      const base = memory.positions.get(node.data('entityId'));
      if (base && !node.grabbed()) node.position(base);
    }));
    if (!floating) { restore(); return; }
    const animate = (time: number) => {
      // Cap redraws at 15 fps. Hidden pages, active layouts and interaction stay still.
      if (time - previous >= 1000 / 15 && graphVisible.current && !document.hidden && memory.settled && !cy.nodes(':grabbed').length) {
        previous = time;
        cy.batch(() => cy.nodes().forEach(node => {
          const id = node.data('entityId') as string, base = memory.positions.get(id);
          if (!base) return;
          if (node.id() === hovered.current) return;
          node.position(memory.pinned.has(id) || id === selected ? base : floatingPosition(id, base, time / 1000));
        }));
      }
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => { cancelAnimationFrame(frame); if (!cy.destroyed()) restore(); };
  }, [floating, selected, memory]);

  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    cy.elements().removeClass('faded focused');
    if (!selected) return;
    const node = cy.getElementById('node:' + selected);
    if (!node.length) return;
    const neighbors = node.closedNeighborhood();
    cy.elements().difference(neighbors).addClass('faded'); node.addClass('focused'); node.connectedEdges().addClass('focused');
  }, [selected, nodes, bundles]);

  useEffect(() => {
    const cy = core.current, node = focus && cy?.getElementById('node:' + focus.id);
    if (!cy || !node?.length) return;
    cy.center(node); if (cy.zoom() < 0.6) cy.zoom({ level: 0.6, position: node.position() });
  }, [focus]);

  async function toggleFullscreen() {
    const host = panel.current?.closest('.entity-graph') ?? panel.current;
    try { if (document.fullscreenElement === host) await document.exitFullscreen(); else await host?.requestFullscreen(); }
    catch { setWarning('浏览器未允许全屏展示，请检查当前浏览器设置。'); }
  }
  async function download(full: boolean) {
    const cy = core.current;
    if (!cy || exporting) return;
    setExporting(true);
    try {
      const blob = await cy.png({ output: 'blob-promise', full, bg: '#f8fafc', maxWidth: 4096, maxHeight: 4096 });
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = full ? 'entity-graph-full.png' : 'entity-graph-view.png'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setWarning('图片导出失败，请缩小图范围后重试。'); }
    finally { setExporting(false); }
  }
  function moveSelected(dx: number, dy: number) {
    if (!selected) return;
    const node = core.current?.getElementById('node:' + selected);
    if (!node?.length) return;
    const previous = memory.positions.get(selected) ?? node.position(), position = { x: previous.x + dx, y: previous.y + dy };
    node.position(position).addClass('pinned'); memory.positions.set(selected, position); memory.pinned.add(selected);
  }
  return <div className="force-panel" ref={panel}>
    <div className="graph-toolbar"><Space wrap><Button aria-pressed={floating} onClick={() => setFloating(value => !value)}>{floating ? '暂停浮动' : '开启浮动'}</Button><Button onClick={() => setVersion(value => value + 1)}>重新布局</Button><Button onClick={() => { memory.pinned.clear(); core.current?.nodes().removeClass('pinned'); setVersion(value => value + 1); }}>解除全部固定</Button><Button onClick={() => core.current?.fit(undefined, 40)}>适配视口</Button><Button aria-label="放大力导图" onClick={() => { const cy = core.current; if (cy) cy.zoom(Math.min(5, cy.zoom() * 1.3)); }}>放大</Button><Button aria-label="缩小力导图" onClick={() => { const cy = core.current; if (cy) cy.zoom(Math.max(0.005, cy.zoom() / 1.3)); }}>缩小</Button></Space><Space wrap><Button onClick={() => void toggleFullscreen()}>{fullscreen ? '退出全屏' : '全屏展示'}</Button><Button loading={exporting} onClick={() => void download(false)}>导出当前 PNG</Button><Button loading={exporting} onClick={() => void download(true)}>导出完整 PNG</Button></Space></div>
    <Space wrap className="section"><Typography.Text type="secondary">{selected ? `选中：${selected}` : '从实体列表定位后，可用按钮移动节点'}</Typography.Text>{[['左移',-20,0],['右移',20,0],['上移',0,-20],['下移',0,20]].map(([label,dx,dy])=><Button key={label} disabled={!selected} aria-label={`选中实体${label}`} onClick={()=>moveSelected(Number(dx),Number(dy))}>{label}</Button>)}<Button disabled={!selected} onClick={()=>{if(selected){memory.pinned.delete(selected);core.current?.getElementById('node:'+selected).removeClass('pinned');}}}>解除选中固定</Button><Button disabled={!selected} onClick={()=>{if(selected) callbacks.current.onNode(selected);}}>选中实体详情</Button></Space>
    {warning && <Alert type="warning" showIcon message={warning}/>}
    <div className="force-canvas" ref={container} role="img" aria-label={`实体关系力导图：${nodes.length}个实体，${bundles.length}条展示连线；详情和审核可通过下方列表操作`}/>
    {!nodes.length && <Empty description="当前范围没有实体"/>}
    <Typography.Text className="force-status" role="status">{status}　{tooltip || '拖动后节点固定；单击节点高亮，双击打开详情；点击连线查看关系。箭头表示原关联方向，不表示加工血缘。'}</Typography.Text>
  </div>;
}
