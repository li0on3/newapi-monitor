import { BarChart3, CircleDollarSign, Filter, Layers3, RefreshCw, Sigma, Users } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { t } from '../i18n'
import { TimeRangeControl } from '../TimeRangeControl'
import { dateRangeQuery, presetRange, rangeLabel, type TimeRange } from '../time-range'
import { AnalyticsTrendChart } from './AnalyticsTrendChart'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading, ConsoleMetric } from './ConsoleCommon'
import type { ConsoleAnalytics as ConsoleAnalyticsData } from './types'
import { buildAnalyticsFlowRows, numberText, quotaText, summarizeFlowOverflow } from './utils'

export function ConsoleAnalytics({ globalScope }: { globalScope: boolean }) {
  const [range, setRange] = useState<TimeRange>(() => presetRange(7))
  const [scope, setScope] = useState<'global' | 'self'>(() => globalScope ? 'global' : 'self')
  const [username, setUsername] = useState('')
  const [data, setData] = useState<ConsoleAnalyticsData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const requestSequence = useRef(0)

  const load = useCallback(async (requestedRange?: TimeRange, requestedScope?: 'global' | 'self') => {
    const requestId = ++requestSequence.current
    const selectedRange = requestedRange || range
    const selectedScope = globalScope ? requestedScope || scope : 'self'
    setLoading(true)
    setError('')
    try {
      const response = await consoleApi.analytics({
        ...dateRangeQuery(selectedRange),
        scope: selectedScope,
        username: globalScope && selectedScope === 'global' ? username.trim() : undefined,
      })
      if (requestId === requestSequence.current) setData(response)
    } catch (reason) {
      if (requestId === requestSequence.current) setError(reason instanceof Error ? reason.message : t('未知错误'))
    } finally {
      if (requestId === requestSequence.current) setLoading(false)
    }
  }, [globalScope, range, scope, username])

  useEffect(() => { void load() }, [])
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
  const visibleModelRows = modelRows.slice(0, 12)
  const modelOverflow = modelRows.slice(12).reduce(
    (summary, item) => ({
      count: summary.count + 1,
      requests: summary.requests + item.requests,
      quota: summary.quota + item.quota,
      tokens: summary.tokens + item.tokens,
    }),
    { count: 0, requests: 0, quota: 0, tokens: 0 },
  )
  const flowRows = useMemo(() => buildAnalyticsFlowRows(data?.flow || []), [data])
  const flowOverflow = summarizeFlowOverflow(flowRows, 12)
  const modelUnattributedQuota = data?.summary.unattributed_quota || 0
  const flowUnattributedQuota = data ? Math.max(0, data.summary.quota - data.summary.flow_quota) : 0
  const modelPendingRequests = data?.summary.unattributed_requests || 0
  const flowPendingRequests = data?.summary.flow_unattributed_requests || 0
  const modelProjectionExcessRequests = Math.max(0, -(data?.summary.model_request_delta || 0))
  const flowProjectionExcessRequests = Math.max(0, -(data?.summary.flow_request_delta || 0))
  const modelProjectionExcessQuota = Math.max(0, -(data?.summary.model_quota_delta || 0))
  const flowProjectionExcessQuota = Math.max(0, -(data?.summary.flow_quota_delta || 0))
  const projectionDiffers = Boolean(
    modelPendingRequests || flowPendingRequests || modelUnattributedQuota || flowUnattributedQuota
    || modelProjectionExcessRequests || flowProjectionExcessRequests
    || modelProjectionExcessQuota || flowProjectionExcessQuota
  )
  const exactTotals = Boolean(data?.reconciliation.requests_exact && data?.reconciliation.quota_exact)
  const totalSourceLabel = exactTotals ? t('实时日志总量') : t('小时归集总量')

  const selectScope = (nextScope: 'global' | 'self') => {
    if (nextScope === scope) return
    setScope(nextScope)
    if (nextScope === 'self') setUsername('')
    void load(undefined, nextScope)
  }

  const submit = (event: FormEvent) => { event.preventDefault(); void load() }
  return <div className="console-page console-analytics-page">
    <form className="console-filter-bar" onSubmit={submit}>
      <div className="console-filter-title"><Filter size={17} /><span><strong>{t('分析范围')}</strong><small>{globalScope && scope === 'global' ? t('管理员可按用户名筛选全局数据') : t('仅展示当前账号的数据')}</small></span></div>
      {globalScope && <div className="segmented analytics-scope-tabs" role="tablist" aria-label={t('数据范围')}>
        <button type="button" role="tab" aria-selected={scope === 'global'} className={scope === 'global' ? 'active' : ''} onClick={() => selectScope('global')}>{t('全局')}</button>
        <button type="button" role="tab" aria-selected={scope === 'self'} className={scope === 'self' ? 'active' : ''} onClick={() => selectScope('self')}>{t('当前账号')}</button>
      </div>}
      <TimeRangeControl compact value={range} onChange={setRange} />
      {globalScope && scope === 'global' && <label><span>{t('用户名')}</span><input value={username} maxLength={128} placeholder={t('留空查看全部')} onChange={(event) => setUsername(event.target.value)} /></label>}
      <button className="primary-button console-filter-submit" type="submit" disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={15} />{t('应用筛选')}</button>
    </form>

    {loading && !data ? <ConsoleLoading /> : error && !data ? <ConsoleError message={error} retry={() => void load()} /> : data && <>
      {error && <div className="console-inline-warning">{error}</div>}
      <section className="console-metric-grid">
        <ConsoleMetric icon={<BarChart3 size={19} />} label={t('请求数')} value={numberText(data.summary.requests)} detail={modelPendingRequests ? t('{{count}} 条尚未归集到模型明细', { count: modelPendingRequests }) : data.reconciliation.requests_exact ? t('实时日志总数') : t('小时归集请求数')} tone="blue" />
        <ConsoleMetric icon={<Sigma size={19} />} label={t('已归集 Token')} value={numberText(data.summary.tokens)} detail={t('按 New API 小时聚合口径')} tone="green" />
        <ConsoleMetric icon={<CircleDollarSign size={19} />} label={t('额度消耗')} value={quotaText(data.summary.quota, data.quota_per_unit)} detail={`${t('当前 RPM')} ${numberText(data.stat.rpm)}`} tone="amber" />
        <ConsoleMetric icon={<Users size={19} />} label={t('数据范围')} value={range.mode === 'all' ? t('累计/历史总量') : data.scope === 'global' ? t('全局') : t('当前账号')} detail={rangeLabel(range, t('累计/历史总量'))} />
      </section>

      <div className="analytics-data-contract"><Layers3 size={17} /><div><strong>{exactTotals ? t('数据口径已对齐') : t('精确总量暂不可用')}</strong><span>{!exactTotals ? t('上游未返回完整的实时日志总量，当前暂以小时归集数据展示；恢复后会自动切回精确总量。') : projectionDiffers ? t('请求数与额度以实时消费日志为准；小时归集明细存在时间差或历史保留范围差异，差额已单独列出。') : t('请求数与额度来自实时消费日志；模型、Token 与额度流向来自 New API 小时归集，当前已完全闭合。')}</span></div><ConsoleBadge tone={!exactTotals || projectionDiffers ? 'amber' : 'green'}>{!exactTotals ? t('小时归集回退') : projectionDiffers ? t('存在归集差额') : t('归集已追平')}</ConsoleBadge></div>

      <section className="console-panel console-chart-panel">
        <div className="console-panel-head"><div><span className="eyebrow">REQUEST TIMELINE</span><h3>{t('请求趋势')}</h3><p>{t('按时间与模型拆分真实请求，可切换请求数、Token 和额度。')}</p></div><ConsoleBadge tone="blue">{new Set(data.series.map((item) => item.created_at)).size} {t('个时间桶')}</ConsoleBadge></div>
        {data.series.length ? <AnalyticsTrendChart series={data.series} quotaPerUnit={data.quota_per_unit} /> : <ConsoleEmpty title={t('该时间范围暂无请求')} detail={t('调整日期范围，或确认当前账号已有调用记录。')} />}
      </section>

      <section className="console-two-column console-analytics-detail-grid">
        <article className="console-panel">
          <div className="console-panel-head"><div><span className="eyebrow">MODEL MIX</span><h3>{t('模型消耗排行')}</h3></div><ConsoleBadge>{totalSourceLabel} {quotaText(data.summary.quota, data.quota_per_unit)}</ConsoleBadge></div>
          {modelRows.length || modelUnattributedQuota > 0 || modelPendingRequests > 0 ? <div className="console-ranking-list">{visibleModelRows.map((item, index) => <div key={item.model}><b>{String(index + 1).padStart(2, '0')}</b><span><strong>{item.model}</strong><small>{numberText(item.requests)} {t('次请求')} · {numberText(item.tokens)} Tokens</small></span><em>{quotaText(item.quota, data.quota_per_unit)}</em></div>)}
            {modelOverflow.count > 0 && <div className="analytics-overflow-row"><b>··</b><span><strong>{t('其他 {{count}} 个模型', { count: modelOverflow.count })}</strong><small>{numberText(modelOverflow.requests)} {t('次请求')} · {numberText(modelOverflow.tokens)} Tokens</small></span><em>{quotaText(modelOverflow.quota, data.quota_per_unit)}</em></div>}
            {(modelUnattributedQuota > 0 || modelPendingRequests > 0) && <div className="analytics-pending-row"><b>··</b><span><strong>{t('总量与归集明细差额')}</strong><small>{modelPendingRequests ? t('{{count}} 条请求已计入实时总数，尚未出现在模型与 Token 归集明细', { count: modelPendingRequests }) : t('实时额度已计入总量，模型归集明细仍存在差额')}</small></span><em>{quotaText(modelUnattributedQuota, data.quota_per_unit)}</em></div>}
            {(modelProjectionExcessQuota > 0 || modelProjectionExcessRequests > 0) && <div className="analytics-pending-row analytics-reconcile-row"><b>−</b><span><strong>{t('历史归集核对差额')}</strong><small>{modelProjectionExcessRequests ? t('小时归集明细比当前可查询消费日志多 {{count}} 条；实时总量保持为权威值', { count: modelProjectionExcessRequests }) : t('小时归集额度高于当前可查询消费日志；实时总量保持为权威值')}</small></span><em>−{quotaText(modelProjectionExcessQuota, data.quota_per_unit)}</em></div>}
          </div> : <ConsoleEmpty title={t('暂无模型数据')} detail={t('当前筛选范围没有可聚合的模型记录。')} />}
        </article>
        <article className="console-panel">
          <div className="console-panel-head"><div><span className="eyebrow">USAGE FLOW</span><h3>{t('额度流向')}</h3></div><ConsoleBadge>{totalSourceLabel} {quotaText(data.summary.quota, data.quota_per_unit)}</ConsoleBadge></div>
          {flowRows.length || flowUnattributedQuota > 0 || flowPendingRequests > 0 ? <div className="console-flow-list">{flowRows.slice(0, 12).map((item) => <div key={item.key}><span><strong>{item.token_name || item.username || `#${item.token_id}`}</strong><small>{item.use_group || t('默认分组')} · {item.model_name}</small></span><em>{quotaText(item.quota, data.quota_per_unit)}<small>{numberText(item.count)} {t('次调用')}</small></em></div>)}
            {flowOverflow.count > 0 && <div className="analytics-overflow-row"><span><strong>{t('其他 {{count}} 个流向', { count: flowOverflow.count })}</strong><small>{numberText(flowOverflow.token_used)} Tokens</small></span><em>{quotaText(flowOverflow.quota, data.quota_per_unit)}<small>{numberText(flowOverflow.requests)} {t('次调用')}</small></em></div>}
            {(flowUnattributedQuota > 0 || flowPendingRequests > 0) && <div className="analytics-pending-row"><span><strong>{t('总量与归集明细差额')}</strong><small>{flowPendingRequests ? t('{{count}} 条请求已计入实时总数，尚未出现在密钥与分组流向明细', { count: flowPendingRequests }) : t('实时额度已计入总量，密钥与分组流向明细仍存在差额')}</small></span><em>{quotaText(flowUnattributedQuota, data.quota_per_unit)}</em></div>}
            {(flowProjectionExcessQuota > 0 || flowProjectionExcessRequests > 0) && <div className="analytics-pending-row analytics-reconcile-row"><span><strong>{t('历史归集核对差额')}</strong><small>{flowProjectionExcessRequests ? t('小时流向明细比当前可查询消费日志多 {{count}} 条；实时总量保持为权威值', { count: flowProjectionExcessRequests }) : t('小时流向额度高于当前可查询消费日志；实时总量保持为权威值')}</small></span><em>−{quotaText(flowProjectionExcessQuota, data.quota_per_unit)}</em></div>}
          </div> : <ConsoleEmpty title={t('暂无流向数据')} detail={t('当前筛选范围没有可展示的流向记录。')} />}
        </article>
      </section>
    </>}
  </div>
}
