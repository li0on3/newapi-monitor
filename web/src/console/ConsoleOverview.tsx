import { ArrowRight, Boxes, CircleDollarSign, Gauge, KeyRound, Layers3, RadioTower } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getLanguage, t } from '../i18n'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading, ConsoleMetric } from './ConsoleCommon'
import type { ConsoleOverview as ConsoleOverviewData, ConsolePageKey } from './types'
import { compactNumber, quotaText } from './utils'

function dateTime(timestamp: number) {
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp * 1000))
}

export function ConsoleOverview({
  onNavigate,
  pages,
}: {
  onNavigate: (page: ConsolePageKey) => void
  pages: Partial<Record<ConsolePageKey, boolean>>
}) {
  const [data, setData] = useState<ConsoleOverviewData | null>(null)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setError('')
    try {
      setData(await consoleApi.overview())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('未知错误'))
    }
  }, [])

  useEffect(() => { void load() }, [load])
  if (!data && !error) return <ConsoleLoading />
  if (!data) return <ConsoleError message={error} retry={() => void load()} />

  const unit = data.system.quota_per_unit || 500000
  const activeKeys = data.keys.items.filter((item) => item.status === 1).length
  return <div className="console-page console-overview-page">
    <section className="console-metric-grid">
      <ConsoleMetric icon={<CircleDollarSign size={19} />} label={t('可用额度')} value={quotaText(data.user.quota, unit)} detail={`${t('累计使用')} ${quotaText(data.user.used_quota, unit)}`} tone="green" />
      <ConsoleMetric icon={<Gauge size={19} />} label={t('累计请求')} value={compactNumber(data.user.request_count)} detail={`${t('最近 24 小时 RPM')} ${compactNumber(data.usage_24h.rpm)}`} tone="blue" />
      {pages.keys !== false && <ConsoleMetric icon={<KeyRound size={19} />} label={t('API 密钥')} value={compactNumber(data.keys.total)} detail={`${t('当前页启用')} ${activeKeys}/${data.keys.items.length}`} tone="amber" />}
      <ConsoleMetric icon={<Boxes size={19} />} label={t('可用模型')} value={compactNumber(data.models.total)} detail={data.user.group || t('默认分组')} />
    </section>

    <section className="console-overview-grid">
      <article className="console-panel console-account-panel">
        <div className="console-panel-head"><div><span className="eyebrow">ACCOUNT SNAPSHOT</span><h3>{t('账号运行概览')}</h3></div><ConsoleBadge tone={data.user.status === 1 ? 'green' : 'red'}>{data.user.status === 1 ? t('正常') : t('已停用')}</ConsoleBadge></div>
        <div className="console-account-identity"><span>{(data.user.display_name || data.user.username).slice(0, 2).toUpperCase()}</span><div><strong>{data.user.display_name || data.user.username}</strong><small>@{data.user.username} · {data.user.group || t('默认分组')}</small></div></div>
        <dl className="console-detail-list">
          <div><dt>{t('数据范围')}</dt><dd>{data.scope === 'global' ? t('全局管理视图') : t('仅当前账号')}</dd></div>
          <div><dt>{t('New API 版本')}</dt><dd>{data.system.version || '—'}</dd></div>
          <div><dt>{t('24 小时用量')}</dt><dd>{quotaText(data.usage_24h.quota, unit)}</dd></div>
          <div><dt>{t('最后同步')}</dt><dd>{dateTime(data.generated_at)}</dd></div>
        </dl>
        {pages.analytics !== false && <button className="console-action-link" type="button" onClick={() => onNavigate('analytics')}>{t('打开数据看板')}<ArrowRight size={15} /></button>}
      </article>

      <article className="console-panel console-quick-panel">
        <div className="console-panel-head"><div><span className="eyebrow">QUICK ACTIONS</span><h3>{t('常用操作')}</h3></div><RadioTower size={19} /></div>
        <div className="console-quick-actions">
          {pages.keys !== false && <button type="button" onClick={() => onNavigate('keys')}><KeyRound size={18} /><span><strong>{t('管理 API 密钥')}</strong><small>{t('创建、编辑、停用与查看密钥')}</small></span><ArrowRight size={15} /></button>}
          {pages.logs !== false && <button type="button" onClick={() => onNavigate('logs')}><Layers3 size={18} /><span><strong>{t('查询使用日志')}</strong><small>{t('按模型、密钥与请求 ID 定位调用')}</small></span><ArrowRight size={15} /></button>}
          {pages.analytics !== false && <button type="button" onClick={() => onNavigate('analytics')}><Gauge size={18} /><span><strong>{t('分析用量趋势')}</strong><small>{t('查看请求、Token 与额度流向')}</small></span><ArrowRight size={15} /></button>}
        </div>
      </article>
    </section>

    <section className="console-two-column">
      {pages.keys !== false && <article className="console-panel">
        <div className="console-panel-head"><div><span className="eyebrow">RECENT KEYS</span><h3>{t('最近密钥')}</h3></div><button type="button" onClick={() => onNavigate('keys')}>{t('查看全部')}</button></div>
        {data.keys.items.length ? <div className="console-key-compact-list">{data.keys.items.map((item) => <button type="button" key={item.id} onClick={() => onNavigate('keys')}><span className="console-key-icon"><KeyRound size={15} /></span><span><strong>{item.name}</strong><small>{item.masked_key || t('密钥已隐藏')}</small></span><ConsoleBadge tone={item.status === 1 ? 'green' : 'neutral'}>{item.status === 1 ? t('启用') : t('停用')}</ConsoleBadge></button>)}</div> : <ConsoleEmpty title={t('暂无 API 密钥')} detail={t('创建密钥后会在这里显示最近项目。')} />}
      </article>}
      <article className="console-panel">
        <div className="console-panel-head"><div><span className="eyebrow">MODEL ACCESS</span><h3>{t('可用模型')}</h3></div><ConsoleBadge tone="blue">{data.models.total}</ConsoleBadge></div>
        {data.models.items.length ? <div className="console-model-cloud">{data.models.items.map((model) => <span key={model}>{model}</span>)}</div> : <ConsoleEmpty title={t('暂无模型')} detail={t('New API 当前没有向该账号暴露模型。')} />}
      </article>
    </section>
  </div>
}
