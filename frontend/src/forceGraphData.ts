import type { Relation } from './types';

export interface EntityNode { id: string; name: string; kind: string; comment: string; column_count: number; }
export interface EntityGraphData { nodes: EntityNode[]; relationships: Relation[]; root: string | null; total_relationships: number; returned_relationships: number; truncated: boolean; }
export interface RelationBundle { id: string; source: string; target: string; origin: string; review: string; relations: Relation[]; }
export interface ForceMemory { positions: Map<string, { x: number; y: number }>; pinned: Set<string>; settled: boolean; }

export function bundleRelations(relations: Relation[]): RelationBundle[] {
  const groups = new Map<string, RelationBundle>();
  for (const relation of relations) {
    const origin = relation.origin ?? 'inferred', review = relation.review ?? 'pending';
    const id = JSON.stringify([relation.source_table, relation.target_table, origin, review]);
    let bundle = groups.get(id);
    if (!bundle) {
      bundle = { id, source: relation.source_table, target: relation.target_table, origin, review, relations: [] };
      groups.set(id, bundle);
    }
    bundle.relations.push(relation);
  }
  return [...groups.values()].sort((a, b) => a.id.localeCompare(b.id));
}

export function gridPositions(ids: string[]) {
  const side = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
  return [...ids].sort().map((id, i) => ({ id, x: (i % side) * 150, y: Math.floor(i / side) * 120 }));
}

// Bounded display motion around a saved layout, never a new force simulation.
export function floatingPosition(id: string, base: { x: number; y: number }, seconds: number) {
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  const phase = (hash % 6283) / 1000;
  return { x: base.x + 5 * Math.sin(seconds * 0.65 + phase), y: base.y + 5 * Math.cos(seconds * 0.5 + phase) };
}
