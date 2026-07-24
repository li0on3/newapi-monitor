import { api } from '../api'
import type {
  ConsoleAnalytics,
  ConsoleLogPage,
  ConsoleOverview,
  ConsoleToken,
  ConsoleTokenDraft,
  ConsoleTokenPage,
} from './types'

function queryString(values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return query.toString()
}

export const consoleApi = {
  overview: () => api<ConsoleOverview>('console/overview'),
  analytics: (filters: Record<string, string | number | undefined>) =>
    api<ConsoleAnalytics>(`console/analytics?${queryString(filters)}`),
  keys: (filters: Record<string, string | number | undefined>) =>
    api<ConsoleTokenPage>(`console/keys?${queryString(filters)}`),
  keyOptions: () => api<{ models: string[]; groups: string[]; quota_per_unit: number }>('console/keys/options'),
  createKey: (payload: ConsoleTokenDraft) =>
    api<{ created: boolean }>('console/keys', { method: 'POST', body: JSON.stringify(payload) }),
  updateKey: (id: number, payload: ConsoleTokenDraft) =>
    api<{ item: ConsoleToken }>(`console/keys/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  updateKeyStatus: (id: number, status: number) =>
    api<{ item: ConsoleToken }>(`console/keys/${id}/status`, { method: 'PUT', body: JSON.stringify({ status }) }),
  deleteKey: (id: number) => api<{ deleted: boolean }>(`console/keys/${id}`, { method: 'DELETE' }),
  batchDeleteKeys: (ids: number[]) =>
    api<{ deleted: number }>('console/keys/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  revealKey: (id: number) => api<{ key: string }>(`console/keys/${id}/reveal`, { method: 'POST' }),
  logs: (filters: Record<string, string | number | undefined>) =>
    api<ConsoleLogPage>(`console/logs?${queryString(filters)}`),
}
