export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1'+path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
  }
  return response.json() as Promise<T>;
}
export function send(method: string, data?: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) };
}
