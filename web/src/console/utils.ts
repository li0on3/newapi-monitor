import type { ConsoleFlowItem, ConsoleLog, ConsoleSeriesItem } from './types'

export type AnalyticsMetric = 'requests' | 'tokens' | 'quota'

export type AnalyticsFlowRow = Pick<ConsoleFlowItem,
  'username' | 'token_id' | 'token_name' | 'use_group' | 'model_name' | 'token_used' | 'count' | 'quota'
> & { key: string }

export function buildAnalyticsFlowRows(flow: ConsoleFlowItem[]): AnalyticsFlowRow[] {
  const rows = new Map<string, AnalyticsFlowRow>()
  for (const item of flow) {
    const identity = item.token_id > 0 ? `token:${item.token_id}` : `user:${item.username}`
    const key = `${identity}\u0000${item.use_group}\u0000${item.model_name}`
    const current = rows.get(key) || {
      key,
      username: item.username,
      token_id: item.token_id,
      token_name: item.token_name,
      use_group: item.use_group,
      model_name: item.model_name,
      token_used: 0,
      count: 0,
      quota: 0,
    }
    current.token_used += Math.max(0, Number(item.token_used) || 0)
    current.count += Math.max(0, Number(item.count) || 0)
    current.quota += Math.max(0, Number(item.quota) || 0)
    rows.set(key, current)
  }
  return [...rows.values()].sort((left, right) => right.quota - left.quota || left.key.localeCompare(right.key))
}

export function summarizeFlowOverflow(rows: AnalyticsFlowRow[], visibleLimit: number) {
  return rows.slice(Math.max(0, visibleLimit)).reduce(
    (summary, row) => ({
      count: summary.count + 1,
      token_used: summary.token_used + row.token_used,
      requests: summary.requests + row.count,
      quota: summary.quota + row.quota,
    }),
    { count: 0, token_used: 0, requests: 0, quota: 0 },
  )
}

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

export function buildAnalyticsModelTimeline(
  series: ConsoleSeriesItem[],
  metric: AnalyticsMetric,
  modelLimit = 5,
) {
  const valueOf = (item: ConsoleSeriesItem) => {
    if (metric === 'tokens') return item.token_used
    if (metric === 'quota') return item.quota
    return item.count
  }
  const modelTotals = new Map<string, number>()
  const buckets = new Map<number, Map<string, number>>()
  for (const item of series) {
    const model = item.model_name || '未知模型'
    const value = Math.max(0, Number(valueOf(item)) || 0)
    modelTotals.set(model, (modelTotals.get(model) || 0) + value)
    const bucket = buckets.get(item.created_at) || new Map<string, number>()
    bucket.set(model, (bucket.get(model) || 0) + value)
    buckets.set(item.created_at, bucket)
  }
  const primaryModels = [...modelTotals.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, Math.max(1, modelLimit))
    .map(([model]) => model)
  const primarySet = new Set(primaryModels)
  const hasOverflow = modelTotals.size > primaryModels.length
  let otherModelKey: string | null = null
  if (hasOverflow) {
    otherModelKey = '\u0000other-models'
    while (modelTotals.has(otherModelKey)) otherModelKey += '\u0000'
  }
  const models = otherModelKey ? [...primaryModels, otherModelKey] : primaryModels
  const points = [...buckets.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([timestamp, bucket]) => {
      const values: Record<string, number> = Object.fromEntries(
        primaryModels.map((model) => [model, bucket.get(model) || 0]),
      )
      if (otherModelKey) {
        values[otherModelKey] = [...bucket.entries()].reduce(
          (total, [model, value]) => total + (primarySet.has(model) ? 0 : value),
          0,
        )
      }
      return {
        timestamp,
        total: [...bucket.values()].reduce((total, value) => total + value, 0),
        values,
      }
    })
  const peak = points.reduce((maximum, point) => Math.max(maximum, point.total), 0)
  const average = points.length
    ? points.reduce((total, point) => total + point.total, 0) / points.length
    : 0
  return { models, points, peak, average, otherModelKey }
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
