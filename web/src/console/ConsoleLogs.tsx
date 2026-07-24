import { ChevronDown, ChevronRight, Download, Filter, Hash, Layers3, RefreshCw, Search, Sigma, Timer } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { getLanguage, t } from '../i18n'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading, ConsoleMetric } from './ConsoleCommon'
import type { ConsoleLogPage } from './types'
import { compactNumber, durationText, logsToCsv, quotaText } from './utils'

function dateInput(timestamp: number) {
  const date = new Date(timestamp * 1000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function timestamp(value: string, end = false) {
  return Math.floor(new Date(`${value}T${end ? '23:59:59' : '00:00:00'}`).getTime() / 1000)
}

function fullTime(value: number) {
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value * 1000))
}

function logType(value: number) {
  const labels: Record<number, string> = { 1: t('充值'), 2: t('消费'), 3: t('管理'), 4: t('系统'), 5: t('错误'), 6: t('退款'), 7: t('登录') }
  return labels[value] || t('全部')
}

export function ConsoleLogs({ globalScope }: { globalScope: boolean }) {
  const now = Math.floor(Date.now() / 1000)
  const [filters, setFilters] = useState({
    startDate: dateInput(now - 6 * 86400), endDate: dateInput(now), logType: '0',
    username: '', tokenName: '', modelName: '', requestId: '', group: '',
  })
  const [applied, setApplied] = useState(filters)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ConsoleLogPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await consoleApi.logs({
        page, page_size: 30, log_type: Number(applied.logType),
        start_timestamp: timestamp(applied.startDate), end_timestamp: timestamp(applied.endDate, true),
        username: globalScope ? applied.username.trim() : undefined,
        token_name: applied.tokenName.trim(), model_name: applied.modelName.trim(),
        request_id: applied.requestId.trim(), group: applied.group.trim(),
      }))
      setExpanded(new Set())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('未知错误'))
    } finally {
      setLoading(false)
    }
  }, [applied, globalScope, page])

  useEffect(() => { void load() }, [load])
  const submit = (event: FormEvent) => { event.preventDefault(); setPage(1); setApplied({ ...filters }) }
  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 30)))

  const exportCsv = () => {
    if (!data?.items.length) return
    const blob = new Blob([`\uFEFF${logsToCsv(data.items)}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `newapi-usage-logs-${Date.now()}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return <div className="console-page console-logs-page">
    <form className="console-log-filters" onSubmit={submit}>
      <div className="console-filter-title"><Filter size={17} /><span><strong>{t('日志筛选')}</strong><small>{globalScope ? t('支持全局账号与调用维度检索') : t('只查询当前账号的真实 New API 日志')}</small></span></div>
      <div className="console-log-filter-grid">
        <label><span>{t('开始日期')}</span><input type="date" value={filters.startDate} max={filters.endDate} onChange={(event) => setFilters({ ...filters, startDate: event.target.value })} /></label>
        <label><span>{t('结束日期')}</span><input type="date" value={filters.endDate} min={filters.startDate} onChange={(event) => setFilters({ ...filters, endDate: event.target.value })} /></label>
        <label><span>{t('日志类型')}</span><select value={filters.logType} onChange={(event) => setFilters({ ...filters, logType: event.target.value })}><option value="0">{t('全部')}</option><option value="2">{t('消费')}</option><option value="5">{t('错误')}</option><option value="1">{t('充值')}</option><option value="6">{t('退款')}</option><option value="3">{t('管理')}</option></select></label>
        {globalScope && <label><span>{t('用户名')}</span><input value={filters.username} maxLength={128} placeholder={t('留空查看全部')} onChange={(event) => setFilters({ ...filters, username: event.target.value })} /></label>}
        <label><span>{t('模型')}</span><input value={filters.modelName} maxLength={256} placeholder="gpt-5.4" onChange={(event) => setFilters({ ...filters, modelName: event.target.value })} /></label>
        <label><span>{t('密钥名称')}</span><input value={filters.tokenName} maxLength={128} placeholder={t('例如：Codex')} onChange={(event) => setFilters({ ...filters, tokenName: event.target.value })} /></label>
        <label><span>{t('分组')}</span><input value={filters.group} maxLength={128} placeholder="default" onChange={(event) => setFilters({ ...filters, group: event.target.value })} /></label>
        <label className="console-log-request-filter"><span>{t('请求 ID')}</span><div><Search size={14} /><input value={filters.requestId} maxLength={128} placeholder="req-..." onChange={(event) => setFilters({ ...filters, requestId: event.target.value })} /></div></label>
      </div>
      <div className="console-filter-actions"><button className="secondary-button" type="button" disabled={!data?.items.length} onClick={exportCsv}><Download size={15} />{t('导出当前页 CSV')}</button><button className="primary-button console-filter-submit" type="submit" disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={15} />{t('查询日志')}</button></div>
    </form>

    {loading && !data ? <ConsoleLoading /> : !data ? <ConsoleError message={error} retry={() => void load()} /> : <>
      {error && <div className="console-inline-warning">{error}</div>}
      {!data.stat_filters_complete && <div className="console-inline-warning">{t('请求 ID 筛选时聚合指标不可用')}</div>}
      <section className="console-metric-grid console-log-metrics">
        <ConsoleMetric icon={<Layers3 size={19} />} label={t('匹配记录')} value={compactNumber(data.total)} detail={data.scope === 'global' ? t('全局日志') : t('当前账号日志')} tone="blue" />
        <ConsoleMetric icon={<Sigma size={19} />} label={t('额度消耗')} value={data.stat ? quotaText(data.stat.quota, data.quota_per_unit) : '—'} detail={`${applied.startDate} → ${applied.endDate}`} tone="amber" />
        <ConsoleMetric icon={<Timer size={19} />} label="RPM" value={data.stat ? compactNumber(data.stat.rpm) : '—'} detail={t('筛选范围统计')} tone="green" />
        <ConsoleMetric icon={<Hash size={19} />} label="TPM" value={data.stat ? compactNumber(data.stat.tpm) : '—'} detail={t('筛选范围统计')} />
      </section>

      <section className="console-panel console-log-list-panel">
        <div className="console-panel-head"><div><span className="eyebrow">REAL USAGE TRACE</span><h3>{t('使用日志')}</h3><p>{t('展开一条记录可查看请求标识、Token、耗时和 New API 返回的诊断字段。')}</p></div><ConsoleBadge tone="blue">{data.total}</ConsoleBadge></div>
        {data.items.length ? <div className="console-log-list">{data.items.map((item) => {
          const open = expanded.has(item.id)
          const frt = Number(item.other.frt || item.other.frt_ms || 0)
          return <article className={open ? 'expanded' : ''} key={`${item.id}-${item.request_id}`}><button className="console-log-summary" type="button" onClick={() => setExpanded((current) => { const next = new Set(current); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next })}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}<span className="console-log-time"><strong>{fullTime(item.created_at)}</strong><small>{item.username || t('当前账号')}</small></span><span><strong>{item.model_name || t('未知模型')}</strong><small>{item.token_name || t('未命名密钥')} · {item.group || t('默认分组')}</small></span><span><strong>{compactNumber(item.prompt_tokens + item.completion_tokens)} Tokens</strong><small>{item.prompt_tokens} + {item.completion_tokens}</small></span><span><strong>{durationText(item.use_time)}</strong><small>{frt > 0 ? `${t('首字')} ${Math.round(frt)} ms` : item.is_stream ? t('流式') : t('非流式')}</small></span><span><ConsoleBadge tone={item.type === 5 ? 'red' : item.type === 2 ? 'green' : 'neutral'}>{logType(item.type)}</ConsoleBadge></span></button>{open && <div className="console-log-detail"><dl><div><dt>{t('请求 ID')}</dt><dd><code>{item.request_id || '—'}</code></dd></div><div><dt>{t('上游请求 ID')}</dt><dd><code>{item.upstream_request_id || '—'}</code></dd></div><div><dt>{t('调用渠道')}</dt><dd>{item.channel_name || (item.channel_id ? `#${item.channel_id}` : '—')}</dd></div><div><dt>{t('额度')}</dt><dd>{quotaText(item.quota, data.quota_per_unit)}</dd></div></dl><section><strong>{t('日志内容')}</strong><pre>{item.content || t('无附加内容')}</pre></section><section><strong>{t('诊断字段')}</strong><pre>{Object.keys(item.other).length ? JSON.stringify(item.other, null, 2) : t('无附加诊断字段')}</pre></section></div>}</article>
        })}</div> : <ConsoleEmpty title={t('没有匹配的使用日志')} detail={t('调整日期或筛选条件后重新查询。')} />}
        <div className="console-pagination"><span>{t('第 {{page}}/{{pages}} 页 · {{total}} 条记录', { page: data.page, pages: pageCount, total: data.total })}</span><div><button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>{t('上一页')}</button><button type="button" disabled={page >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>{t('下一页')}</button></div></div>
      </section>
    </>}
  </div>
}
