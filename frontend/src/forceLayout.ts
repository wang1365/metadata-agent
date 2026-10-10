import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationNodeDatum } from 'd3-force';

export interface LayoutNode extends SimulationNodeDatum { id: string; }
export interface LayoutInput { nodes: { id: string; x?: number; y?: number; fx?: number; fy?: number }[]; links: { source: string; target: string }[]; }
export interface LayoutResult { positions: { id: string; x: number; y: number }[]; iterations: number; duration_ms: number; timed_out: boolean; }

export function computeForceLayout(input: LayoutInput, progress: (iteration: number) => void = () => {}, now = () => performance.now()): LayoutResult {
  const start = now();
  const nodes: LayoutNode[] = input.nodes.map(node => ({ ...node })).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const ids = new Set(nodes.map(node => node.id));
  const links = new Map<string, { source: string; target: string }>();
  for (const edge of input.links) {
    if (edge.source === edge.target || !ids.has(edge.source) || !ids.has(edge.target)) continue;
    const pair = [edge.source, edge.target].sort();
    links.set(JSON.stringify(pair), { source: pair[0], target: pair[1] });
  }
  let seed = 1;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  const simulation = forceSimulation(nodes).stop().randomSource(random)
    .force('link', forceLink<LayoutNode, { source: string; target: string }>([...links.values()].sort((a, b) => a.source.localeCompare(b.source) || a.target.localeCompare(b.target))).id(node => node.id).distance(140).strength(0.08))
    .force('charge', forceManyBody().strength(-250))
    .force('collide', forceCollide(36))
    .force('center', forceCenter(0, 0));
  let iterations = 0;
  try {
    while (iterations < 300 && now() - start < 5000) {
      simulation.tick(); iterations++;
      if (iterations % 20 === 0) progress(iterations);
    }
    return { positions: nodes.map(node => ({ id: node.id, x: node.x ?? 0, y: node.y ?? 0 })), iterations,
      duration_ms: Math.round(now() - start), timed_out: iterations < 300 };
  } finally { simulation.stop(); }
}
