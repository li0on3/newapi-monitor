export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function apiUrl(path: string): string {
  return new URL(`api/${path.replace(/^\//, '')}`, window.location.href).toString();
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const userId = window.localStorage.getItem('uid');
  const method = (init?.method || 'GET').toUpperCase();
  const response = await fetch(apiUrl(path), {
    credentials: 'same-origin',
    headers: {
      ...(userId ? { 'New-Api-User': userId } : {}),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(!['GET', 'HEAD', 'OPTIONS'].includes(method) ? { 'X-Monitor-Request': '1' } : {}),
      ...init?.headers,
    },
    ...init,
  });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string } & T;
  if (!response.ok) {
    throw new ApiError(response.status, payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}
