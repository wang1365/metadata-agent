import { computeForceLayout, type LayoutInput } from './forceLayout';

self.onmessage = (event: MessageEvent<{ generation: number; input: LayoutInput }>) => {
  const { generation, input } = event.data;
  try {
    const result = computeForceLayout(input, iterations => self.postMessage({ generation, type: 'progress', iterations }));
    self.postMessage({ generation, type: 'done', ...result });
  } catch {
    self.postMessage({ generation, type: 'error' });
  }
};
