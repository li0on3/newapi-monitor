import { BarChart3, CircleDollarSign, Filter, Layers3, RefreshCw, Sigma, Users } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { getLanguage, t } from '../i18n'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading, ConsoleMetric } from './ConsoleCommon'
import type { ConsoleAnalytics as ConsoleAnalyticsData } from './types'
import { buildAnalyticsTimeline, compactNumber, quotaText } from './utils'

function dateInput(timestamp: number) {
  const date = new Date(timestamp * 1000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function startOfDay(value: string) {
  return Math.floor(new Date(`${value}T00:00:00`).getTime() / 1000)
}

export function ConsoleAnalytics({ globalScope }: { globalScope: boolean }) {
  const now = Math.floor(Date.now() / 1000)
  const [startDate, setStartDate] = useState(dateInput(now - 6 * 86400))
  const [endDate, setEndDate] = useState(dateInput(now))
  const [username, setUsername] = useState('')
  const [data, setData] = useState<ConsoleAnalyticsData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await consoleApi.analytics({
        start_timestamp: startOfDay(startDate),
        end_timestamp: startOfDay(endDate) + 86399,
        username: globalScope ? username.trim() : undefined,
      }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('未知错误'))
    } finally {
      setLoading(false)
    }
  }, [endDate, globalScope, startDate, username])

  useEffect(() => { void load() }, [])
  const timeline = useMemo(() => buildAnalyticsTimeline(data?.series || []), [data])
  const maxRequests = Math.max(1, ...timeline.map((item) => item.requests))
  const modelRows = useMemo(() => {
    const values = new Map<string, { model: string; requests: number; quota: number; tokens: number }>()
    for (const item of data?.series || []) {
      const model = item.model_name || t('未知模型')
      const current = values.get(model) || { model, requests: 0, quota: 0, tokens: 0 }
      current.requests += item.count
      current.quota += item.quota
      current.tokens += item.token_used
      values.set(model, current)
    }
    return [...values.values()].sort((left, right) => right.quota - left.quota)
  }, [data])

  const submit = (event: FormEvent) => { event.preventDefault(); void load() }
  return <div className="console-page console-analytics-page">
    <form className="console-filter-bar" onSubmit={submit}>
      <div className="console-filter-title"><Filter size={17} /><span><strong>{t('分析范围')}</strong><small>{globalScope ? t('管理员可按用户名筛选全局数据') : t('仅展示当前 New API 账号的数据')}</small></span></div>
      <label><span>{t('开始日期')}</span><input type="date" value={startDate} max={endDate} onChange={(event) => setStartDate(event.target.value)} /></label>
      <label><span>{t('结束日期')}</span><input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} /></label>
      {globalScope && <label><span>{t('用户名')}</span><input value={username} maxLength={128} placeholder={t('留空查看全部')} onChange={(event) => setUsername(event.target.value)} /></label>}
      <button className="primary-button console-filter-submit" type="submit" disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={15} />{t('应用筛选')}</button>
    </form>

    {loading && !data ? <ConsoleLoading /> : error && !data ? <ConsoleError message={error} retry={() => void load()} /> : data && <>
      {error && <div className="console-inline-warning">{error}</div>}
      <section className="console-metric-grid">
        <ConsoleMetric icon={<BarChart3 size={19} />} label={t('请求数')} value={compactNumber(data.summary.requests)} detail={`${data.summary.models} ${t('个模型')}`} tone="blue" />
        <ConsoleMetric icon={<Sigma size={19} />} label={t('Token 用量')} value={compactNumber(data.summary.tokens)} detail={`TPM ${compactNumber(data.stat.tpm)}`} tone="green" />
        <ConsoleMetric icon={<CircleDollarSign size={19} />} label={t('额度消耗')} value={quotaText(data.summary.quota, data.quota_per_unit)} detail={`RPM ${compactNumber(data.stat.rpm)}`} tone="amber" />
        <ConsoleMetric icon={<Users size={19} />} label={t('数据范围')} value={data.scope === 'global' ? t('全局') : t('当前账号')} detail={`${startDate} → ${endDate}`} />
      </section>

      <section className="console-panel console-chart-panel">
        <div className="console-panel-head"><div><span className="eyebrow">REQUEST TIMELINE</span><h3>{t('请求趋势')}</h3><p>{t('按 New API 数据看板时间桶汇总，不在监控平台重复存储业务数据。')}</p></div><ConsoleBadge tone="blue">{timeline.length} {t('个数据点')}</ConsoleBadge></div>
        {timeline.length ? <div className="console-bar-chart" role="img" aria-label={t('请求趋势图')}>{timeline.map((item) => <div className="console-bar-column" key={item.timestamp} title={`${new Date(item.timestamp * 1000).toLocaleString()} · ${item.requests} ${t('请求')}`}><span style={{ height: `${Math.max(4, item.requests / maxRequests * 100)}%` }} /><small>{new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', { month: '2-digit', day: '2-digit' }).format(new Date(item.timestamp * 1000))}</small></div>)}</div> : <ConsoleEmpty title={t('该时间范围暂无请求')} detail={t('调整日期范围，或确认 New API 已启用数据看板采集。')} />}
      </section>

      <section className="console-two-column console-analytics-detail-grid">
        <article className="console-panel">
          <div className="console-panel-head"><div><span className="eyebrow">MODEL MIX</span><h3>{t('模型消耗排行')}</h3></div><Layers3 size={18} /></div>
          {modelRows.length ? <div className="console-ranking-list">{modelRows.slice(0, 12).map((item, index) => <div key={item.model}><b>{String(index + 1).padStart(2, '0')}</b><span><strong>{item.model}</strong><small>{compactNumber(item.requests)} {t('次请求')} · {compactNumber(item.tokens)} Tokens</small></span><em>{quotaText(item.quota, data.quota_per_unit)}</em></div>)}</div> : <ConsoleEmpty title={t('暂无模型数据')} detail={t('当前筛选范围没有可聚合的模型记录。')} />}
        </article>
        <article className="console-panel">
          <div className="console-panel-head"><div><span className="eyebrow">USAGE FLOW</span><h3>{t('额度流向')}</h3></div><ConsoleBadge>{data.flow.length}</ConsoleBadge></div>
          {data.flow.length ? <div className="console-flow-list">{data.flow.slice(0, 12).map((item, index) => <div key={`${item.token_id}-${item.model_name}-${index}`}><span><strong>{item.token_name || item.username || `#${item.token_id}`}</strong><small>{item.use_group || t('默认分组')} · {item.model_name}</small></span><em>{quotaText(item.quota, data.quota_per_unit)}<small>{compactNumber(item.count)} {t('次调用')}</small></em></div>)}</div> : <ConsoleEmpty title={t('暂无流向数据')} detail={t('New API 当前没有返回分组流向记录。')} />}
        </article>
      </section>
    </>}
  </div>
}
