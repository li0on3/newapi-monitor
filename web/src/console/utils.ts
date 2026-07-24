import type { ConsoleLog, ConsoleSeriesItem } from './types'

export function buildAnalyticsTimeline(series: ConsoleSeriesItem[]) {
  const buckets = new Map<number, { timestamp: number; requests: number; quota: number; tokens: number }>()
  for (const item of series) {
    const current = buckets.get(item.created_at) || {
      timestamp: item.created_at,
      requests: 0,
      quota: 0,
      tokens: 0,
    }
    current.requests += item.count
    current.quota += item.quota
    current.tokens += item.token_used
    buckets.set(item.created_at, current)
  }
  return [...buckets.values()].sort((left, right) => left.timestamp - right.timestamp)
}

function csvCell(value: unknown): string {
  const raw = String(value ?? '')
  const text = /^[=+\-@]/.test(raw) ? `'${raw}` : raw
  return `"${text.replaceAll('"', '""')}"`
}

export function logsToCsv(items: ConsoleLog[]): string {
  const columns: Array<keyof ConsoleLog> = [
    'created_at', 'username', 'token_name', 'model_name', 'quota', 'prompt_tokens',
    'completion_tokens', 'use_time', 'is_stream', 'group', 'request_id',
    'upstream_request_id', 'content',
  ]
  return [
    columns.join(','),
    ...items.map((item) => columns.map((column) => csvCell(item[column])).join(',')),
  ].join('\n')
}

export function quotaText(value: number, quotaPerUnit = 500000): string {
  const amount = quotaPerUnit > 0 ? value / quotaPerUnit : 0
  return `$${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
}

export function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0)
}

export function durationText(seconds: number): string {
  if (seconds < 1) return `${Math.max(0, Math.round(seconds * 1000))} ms`
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`
}
