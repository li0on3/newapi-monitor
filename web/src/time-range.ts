export type TimeRange = {
  mode: 'custom' | 'all'
  startDate: string
  endDate: string
  label?: string
}

function localDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function presetRange(days: number, now = new Date()): TimeRange {
  const start = new Date(now)
  start.setDate(start.getDate() - Math.max(1, days) + 1)
  return { mode: 'custom', startDate: localDate(start), endDate: localDate(now), label: `${days}d` }
}

export function allTimeRange(): TimeRange {
  return { mode: 'all', startDate: '', endDate: '', label: 'all' }
}

export function dateRangeQuery(range: TimeRange): Record<string, number> {
  if (range.mode === 'all') return { all_time: 1 }
  return {
    start_timestamp: Math.floor(new Date(`${range.startDate}T00:00:00`).getTime() / 1000),
    end_timestamp: Math.floor(new Date(`${range.endDate}T23:59:59`).getTime() / 1000),
  }
}

export function appendDateRange(query: URLSearchParams, range: TimeRange) {
  for (const [key, value] of Object.entries(dateRangeQuery(range))) query.set(key, String(value))
  return query
}

export function isLiveRange(range: TimeRange, now = new Date()) {
  return range.mode === 'all' || range.endDate === localDate(now)
}

export function rangeLabel(range: TimeRange, allLabel: string) {
  return range.mode === 'all' ? allLabel : `${range.startDate} → ${range.endDate}`
}
