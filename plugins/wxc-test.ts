import type {Context} from '@deepseek-ai/cordis'

export const name = 'wxc-test'

export function apply(ctx: Context) {
    // stdout is reserved for the SDK JSON-RPC transport.
    console.error('wxc-test loaded')
}
