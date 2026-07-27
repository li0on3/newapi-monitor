import { CalendarDays, History } from 'lucide-react'
import { t } from './i18n'
import { allTimeRange, presetRange, type TimeRange } from './time-range'

export function TimeRangeControl({ value, onChange, compact = false }: {
  value: TimeRange
  onChange: (value: TimeRange) => void
  compact?: boolean
}) {
  return <div className={compact ? 'time-range-control compact' : 'time-range-control'}>
    <div className="time-range-presets" aria-label={t('快捷日期范围')}>
      {[1, 7, 30, 90].map((days) => <button
        className={value.mode === 'custom' && value.label === `${days}d` ? 'active' : ''}
        type="button"
        key={days}
        onClick={() => onChange(presetRange(days))}
      >{days === 1 ? t('今天') : t('近 {{days}} 天', { days })}</button>)}
      <button className={value.mode === 'all' ? 'active' : ''} type="button" onClick={() => onChange(allTimeRange())}><History size={13} />{t('创建至今')}</button>
    </div>
    {value.mode === 'custom' && <div className="time-range-dates">
      <CalendarDays size={14} />
      <label><span>{t('开始日期')}</span><input type="date" value={value.startDate} max={value.endDate} onChange={(event) => onChange({ ...value, label: undefined, startDate: event.target.value })} /></label>
      <i>→</i>
      <label><span>{t('结束日期')}</span><input type="date" value={value.endDate} min={value.startDate} onChange={(event) => onChange({ ...value, label: undefined, endDate: event.target.value })} /></label>
    </div>}
  </div>
}
