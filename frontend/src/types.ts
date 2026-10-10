export interface Source { id: string; name: string; type: 'excel' | 'mysql'; config?: { database: string; host?: string; port?: number; username?: string }; sheets?: {name: string; rows: number; columns: number}[]; size?: number; created?: string; }
export interface Job { id: string; source_id: string | null; status: string; stage: string; error?: string; created: string; snapshot_id?: string; bound_snapshot_id?: string; conversation_id?: string; question?: string; answer_id?: string; queue?: string; queries: number; sample_rows: number; cancel_requested: boolean; }
export interface Column { name: string; type: string; comment: string; evidence_ids: string[]; }
export interface MetaTable { name: string; kind: string; comment: string; columns: Column[]; keys: { name: string; kind: string; columns: string[] }[]; evidence_ids: string[]; }
export interface Relation { id?: string; source_table: string; target_table: string; source_columns?: string[]; target_columns?: string[]; source_column?: string; target_column?: string; origin?: string; review?: string; explanation?: string; evidence_ids: string[]; }
export interface Snapshot { id: string; source_id: string; tables: number; created: string; }
export interface Graph { tables: MetaTable[]; edges: Relation[]; root: string; }
export interface Preview { version: number; findings: { tables: MetaTable[]; relationships: Relation[]; lineage: Relation[]; warnings: string[] }; evidence: Record<string, unknown>; }
export interface Answer { id: string; question: string; answer: string; evidence_ids: string[]; }
