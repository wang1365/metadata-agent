import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { Worker } from 'node:worker_threads';
import ts from 'typescript';

// Compile pure production helpers into an ignored cache; no test hooks in the app.
const cache = new URL('../node_modules/.cache/force-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
for (const name of ['forceGraphData', 'forceLayout', 'forceLayout.worker']) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText.replace("'./forceLayout'", "'./forceLayout.mjs'");
  await writeFile(new URL(`${name}.mjs`, cache), compiled);
}
const { bundleRelations, gridPositions, floatingPosition } = await import(new URL('forceGraphData.mjs', cache));
const { computeForceLayout } = await import(new URL('forceLayout.mjs', cache));
const relation = { source_table: 'a', target_table: 'b', origin: 'constraint', review: 'pending', source_columns: ['id'], target_columns: ['id'], evidence_ids: ['synthetic'] };
const groups = bundleRelations([{ ...relation, id: 'first' }, { ...relation, id: 'second', source_columns: ['id', 'kind'], target_columns: ['id', 'kind'] }, { ...relation, id: 'reverse', source_table: 'b', target_table: 'a' }, { ...relation, id: 'other', origin: 'inferred' }, { ...relation, id: 'rejected', review: 'rejected' }]);
assert.equal(groups.length, 4);
assert.deepEqual(groups.find(group => group.relations.length === 2).relations.map(item => item.id), ['first', 'second']);
assert.deepEqual(gridPositions(['b', 'a']), gridPositions(['a', 'b']));
const anchor = { x: 120, y: 80 };
for (let seconds = 0; seconds < 300; seconds += 0.1) {
  const drift = floatingPosition('a', anchor, seconds);
  assert(Math.abs(drift.x - anchor.x) <= 5 && Math.abs(drift.y - anchor.y) <= 5);
}
assert.deepEqual(anchor, { x: 120, y: 80 });
assert.deepEqual(floatingPosition('a', anchor, 2), floatingPosition('a', anchor, 2));
assert.notDeepEqual(floatingPosition('a', anchor, 2), floatingPosition('a', anchor, 3));
const input = { nodes: [{ id: 'b' }, { id: 'a', x: 120, y: 80, fx: 120, fy: 80 }], links: [{ source: 'a', target: 'b' }, { source: 'a', target: 'a' }] };
const first = computeForceLayout(input), second = computeForceLayout(input);
assert.deepEqual(first.positions, second.positions);
assert.deepEqual(first.positions.find(node => node.id === 'a'), { id: 'a', x: 120, y: 80 });
assert.equal(input.nodes[0].x, undefined); // d3-force must never mutate cached API data.
let clock = 0;
const timed = computeForceLayout(input, () => {}, () => { clock += 1000; return clock; });
assert(timed.timed_out && timed.iterations < 300);
assert.deepEqual(computeForceLayout({ nodes: [], links: [] }).positions, []);

const large = { nodes: Array.from({ length: 500 }, (_, i) => ({ id: `table_${i}` })), links: Array.from({ length: 5000 }, (_, i) => ({ source: `table_${i % 499}`, target: `table_${(i % 499 + Math.floor(i / 499) + 1) % 499}` })) };
const wrapper = new URL('worker-wrapper.mjs', cache);
await writeFile(wrapper, `import { parentPort } from 'node:worker_threads'; globalThis.self = globalThis; self.postMessage = message => parentPort.postMessage(message); await import('./forceLayout.worker.mjs'); parentPort.on('message', data => self.onmessage({data}));`);
const worker = new Worker(wrapper);
const messages = [];
const result = await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => { worker.terminate(); reject(new Error('Worker failed to finish')); }, 8000);
  worker.on('error', error => {clearTimeout(timeout); reject(error);});
  worker.on('message', message => { messages.push(message); if (message.type === 'done') {clearTimeout(timeout); resolve(message);} });
  worker.postMessage({ generation: 7, input: large });
});
assert.equal(result.generation, 7);
assert.equal(result.positions.length, 500);
assert(result.positions.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)));
assert(messages.some(message => message.type === 'progress'));
assert(result.iterations <= 300 && result.duration_ms < 6000);
await worker.terminate();
const cancelled = new Worker(wrapper);
cancelled.postMessage({ generation: 8, input: large });
await cancelled.terminate();
console.log(`FORCE_TESTS_OK nodes=500 relations=5000 iterations=${result.iterations} worker_ms=${result.duration_ms} bundles=${groups.length}`);
