// Native Cordis plugin. Delegate MCP lifecycle/protocol to DSH's official bridge.
// Copied next to the generated profile's node_modules for package resolution.
import * as bridge from '@deepseek-ai/dsh-mcp-client';

export const name = 'metadata-tools';
export const inject = bridge.inject;
export const Config = bridge.Config;

export async function apply(ctx, config) {
  if (config.serverName !== 'metadata' || config.transport !== 'stdio') {
    throw new Error('metadata-tools requires its task-scoped stdio server');
  }
  await bridge.apply(ctx, config);
}
