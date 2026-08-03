import { Activity, CircleDollarSign, Sigma } from 'lucide-react'
import { useMemo, useState } from 'react'
import { getLanguage, t } from '../i18n'
import type { ConsoleSeriesItem } from './types'
import { buildAnalyticsModelTimeline, compactNumber, numberText, quotaText } from './utils'
import type { AnalyticsMetric } from './utils'

const WIDTH = 960
const HEIGHT = 310
const PADDING = { top: 22, right: 24, bottom: 42, left: 64 }
const COLORS = ['#4f8df7', '#2fbf87', '#f59e0b', '#a78bfa', '#f06a86', '#82909d']

const METRICS: Array<{ id: AnalyticsMetric; label: string; icon: typeof Activity }> = [
  { id: 'requests', label: '请求数', icon: Activity },
  { id: 'tokens', label: 'Token', icon: Sigma },
  { id: 'quota', label: '额度', icon: CircleDollarSign },
]

function metricText(metric: AnalyticsMetric, value: number, quotaPerUnit: number) {
  if (metric === 'quota') return quotaText(value, quotaPerUnit)
  return numberText(value)
}

function axisMetricText(metric: AnalyticsMetric, value: number, quotaPerUnit: number) {
  if (metric === 'quota') return quotaText(value, quotaPerUnit)
  return compactNumber(value)
}

function timeText(timestamp: number, detailed = false) {
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', detailed
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { month: '2-digit', day: '2-digit' }).format(new Date(timestamp * 1000))
}

function axisTimeText(timestamp: number, spanSeconds: number) {
  const locale = getLanguage() === 'en' ? 'en-US' : 'zh-CN'
  const options: Intl.DateTimeFormatOptions = spanSeconds <= 2 * 86400
    ? { hour: '2-digit', minute: '2-digit', hour12: false }
    : { month: '2-digit', day: '2-digit' }
  return new Intl.DateTimeFormat(locale, options).format(new Date(timestamp * 1000))
}

export function AnalyticsTrendChart({
  series,
  quotaPerUnit,
}: {
  series: ConsoleSeriesItem[]
  quotaPerUnit: number
}) {
  const [metric, setMetric] = useState<AnalyticsMetric>('requests')
  const [hovered, setHovered] = useState<number | null>(null)
  const trend = useMemo(() => buildAnalyticsModelTimeline(series, metric, 5), [metric, series])
  const points = trend.points
  const plotWidth = WIDTH - PADDING.left - PADDING.right
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom
  const maximum = Math.max(1, trend.peak)
  const x = (index: number) => PADDING.left + (points.length <= 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth)
  const y = (value: number) => PADDING.top + plotHeight - value / maximum * plotHeight
  const totalsPath = points.map((point, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(point.total)}`).join(' ')
  const stacked = useMemo(() => {
    const lower = points.map(() => 0)
    return trend.models.map((model, modelIndex) => {
      const upper = points.map((point, index) => lower[index] + (point.values[model] || 0))
      const forward = upper.map((value, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(value)}`).join(' ')
      const reverse = lower.map((value, index) => `L ${x(points.length - index - 1)} ${y(lower[points.length - index - 1])}`).join(' ')
      const path = `${forward} ${reverse} Z`
      for (let index = 0; index < upper.length; index += 1) lower[index] = upper[index]
      return { model, path, color: COLORS[modelIndex % COLORS.length] }
    })
  }, [points, trend.models, maximum])
  const xLabels = useMemo(() => {
    if (!points.length) return []
    const count = Math.min(6, points.length)
    return [...new Set(Array.from({ length: count }, (_, index) => Math.round(index * (points.length - 1) / Math.max(1, count - 1))))]
  }, [points])
  const spanSeconds = points.length > 1 ? points[points.length - 1].timestamp - points[0].timestamp : 0
  const hoveredPoint = hovered == null ? null : points[hovered]
  const modelLabel = (model: string) => model === trend.otherModelKey ? t('其他模型') : model
  const moveSelection = (offset: number) => {
    if (!points.length) return
    setHovered((current) => Math.max(0, Math.min(points.length - 1, (current ?? 0) + offset)))
  }
  const selectPointer = (element: SVGRectElement, clientX: number) => {
    if (!points.length) return
    const bounds = element.ownerSVGElement?.getBoundingClientRect()
    if (!bounds) return
    const pointer = (clientX - bounds.left) * WIDTH / bounds.width
    const index = points.length <= 1 ? 0 : Math.round((pointer - PADDING.left) / plotWidth * (points.length - 1))
    setHovered(Math.max(0, Math.min(points.length - 1, index)))
  }

  return <div className="analytics-trend">
    <div className="analytics-trend-toolbar">
      <div className="analytics-metric-tabs" role="tablist" aria-label={t('趋势指标')}>
        {METRICS.map(({ id, label, icon: Icon }) => <button className={metric === id ? 'active' : ''} type="button" role="tab" aria-selected={metric === id} key={id} onClick={() => setMetric(id)}><Icon size={14} />{t(label)}</button>)}
      </div>
      <div className="analytics-trend-summary"><span><small>{t('峰值')}</small><strong>{metricText(metric, trend.peak, quotaPerUnit)}</strong></span><span><small>{t('每时间桶平均')}</small><strong>{metricText(metric, trend.average, quotaPerUnit)}</strong></span></div>
    </div>
    <div className="analytics-legend" aria-label={t('模型图例')}>{stacked.map((item) => <span key={item.model}><i style={{ backgroundColor: item.color }} />{modelLabel(item.model)}</span>)}</div>
    <div className="analytics-trend-scroll">
      <div className="analytics-chart-stage">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" tabIndex={points.length ? 0 : -1} aria-label={t('按模型堆叠的请求趋势图')} onFocus={() => { if (points.length) setHovered((current) => current ?? 0) }} onBlur={() => setHovered(null)} onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') { event.preventDefault(); moveSelection(-1) }
          if (event.key === 'ArrowRight') { event.preventDefault(); moveSelection(1) }
          if (event.key === 'Home') { event.preventDefault(); setHovered(points.length ? 0 : null) }
          if (event.key === 'End') { event.preventDefault(); setHovered(points.length ? points.length - 1 : null) }
        }}>
          <defs>
            {stacked.map((item, index) => <linearGradient id={`analytics-series-${index}`} x1="0" y1="0" x2="0" y2="1" key={item.model}><stop offset="0" stopColor={item.color} stopOpacity="0.55" /><stop offset="1" stopColor={item.color} stopOpacity="0.08" /></linearGradient>)}
          </defs>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => <g key={ratio}><line className="analytics-grid-line" x1={PADDING.left} x2={WIDTH - PADDING.right} y1={y(maximum * ratio)} y2={y(maximum * ratio)} /><text className="analytics-axis-label" x={PADDING.left - 10} y={y(maximum * ratio) + 4} textAnchor="end">{axisMetricText(metric, maximum * ratio, quotaPerUnit)}</text></g>)}
          {stacked.map((item, index) => <path d={item.path} fill={`url(#analytics-series-${index})`} stroke={item.color} strokeOpacity="0.72" strokeWidth="1.25" key={item.model} />)}
          {totalsPath && <path d={totalsPath} className="analytics-total-line" fill="none" />}
          {points.length === 1 && <circle className="analytics-single-point" cx={x(0)} cy={y(points[0].total)} r="5" />}
          {xLabels.map((index) => <text className="analytics-axis-label" x={x(index)} y={HEIGHT - 14} textAnchor="middle" key={index}>{axisTimeText(points[index].timestamp, spanSeconds)}</text>)}
          {hoveredPoint && <g><line className="analytics-hover-line" x1={x(hovered!)} x2={x(hovered!)} y1={PADDING.top} y2={PADDING.top + plotHeight} /><circle className="analytics-hover-point" cx={x(hovered!)} cy={y(hoveredPoint.total)} r="4.5" /></g>}
          <rect className="analytics-hit-area" x={PADDING.left} y={PADDING.top} width={plotWidth} height={plotHeight} onPointerLeave={() => setHovered(null)} onPointerDown={(event) => {
            event.currentTarget.setPointerCapture?.(event.pointerId)
            selectPointer(event.currentTarget, event.clientX)
          }} onPointerMove={(event) => {
            selectPointer(event.currentTarget, event.clientX)
          }} />
        </svg>
        {hoveredPoint && <div className={`analytics-tooltip ${hovered! > points.length * 0.72 ? 'align-right' : ''}`} style={{ left: `${x(hovered!) / WIDTH * 100}%` }}><div><strong>{timeText(hoveredPoint.timestamp, true)}</strong><b>{metricText(metric, hoveredPoint.total, quotaPerUnit)}</b></div>{stacked.map((item) => <span key={item.model}><i style={{ backgroundColor: item.color }} /><em>{modelLabel(item.model)}</em><b>{metricText(metric, hoveredPoint.values[item.model] || 0, quotaPerUnit)}</b></span>)}</div>}
        <table className="sr-only"><caption>{t('趋势数据表')}</caption><thead><tr><th>{t('时间')}</th>{stacked.map((item) => <th key={item.model}>{modelLabel(item.model)}</th>)}<th>{t('总计')}</th></tr></thead><tbody>{points.map((point) => <tr key={point.timestamp}><td>{timeText(point.timestamp, true)}</td>{stacked.map((item) => <td key={item.model}>{metricText(metric, point.values[item.model] || 0, quotaPerUnit)}</td>)}<td>{metricText(metric, point.total, quotaPerUnit)}</td></tr>)}</tbody></table>
      </div>
    </div>
  </div>
}
