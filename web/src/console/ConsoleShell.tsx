import { BarChart3, KeyRound, LayoutDashboard, ScrollText, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { t } from '../i18n'
import type { ConsolePage } from '../routes'
import { ConsoleAnalytics } from './ConsoleAnalytics'
import { ConsoleKeys } from './ConsoleKeys'
import { ConsoleLogs } from './ConsoleLogs'
import { ConsoleOverview } from './ConsoleOverview'
import type { ConsolePageKey } from './types'

const ITEMS = [
  { id: 'overview' as const, label: t('概览'), detail: t('账号、额度与快捷入口'), icon: LayoutDashboard },
  { id: 'analytics' as const, label: t('数据看板'), detail: t('请求、Token 与额度趋势'), icon: BarChart3 },
  { id: 'keys' as const, label: t('API 密钥'), detail: t('创建与管理客户端凭据'), icon: KeyRound },
  { id: 'logs' as const, label: t('使用日志'), detail: t('检索真实调用详情'), icon: ScrollText },
]

export function ConsoleShell({
  page,
  pages,
  globalScope,
  onNavigate,
}: {
  page: ConsolePage
  pages: Partial<Record<ConsolePageKey, boolean>>
  globalScope: boolean
  onNavigate: (page: ConsolePage) => void
}) {
  const visible = useMemo(() => ITEMS.filter((item) => pages[item.id] !== false), [pages])
  const activePage = visible.some((item) => item.id === page) ? page : (visible[0]?.id || 'overview')
  useEffect(() => {
    if (activePage !== page) onNavigate(activePage)
  }, [activePage, onNavigate, page])
  const current = ITEMS.find((item) => item.id === activePage) || ITEMS[0]

  return <section className="console-workspace">
    <header className="console-hero">
      <div><span className="eyebrow">CUSTOMER CONSOLE</span><h1>{t('New API 客户控制台')}</h1><p>{t('直接使用 New API 会话与真实数据，在不修改上游代码的前提下完成自助查询和密钥管理。')}</p></div>
      <div className="console-scope-chip"><ShieldCheck size={17} /><span><strong>{globalScope ? t('管理员数据范围') : t('个人数据范围')}</strong><small>{t('权限始终由 New API 校验')}</small></span></div>
    </header>
    <div className="console-layout">
      <aside className="console-side-nav" aria-label={t('客户控制台导航')}>
        <div className="console-side-title"><span>{t('客户功能')}</span><small>{visible.length}/4</small></div>
        {visible.map((item) => <button className={activePage === item.id ? 'active' : ''} type="button" key={item.id} onClick={() => onNavigate(item.id)}><span><item.icon size={17} /></span><span><strong>{item.label}</strong><small>{item.detail}</small></span></button>)}
        <div className="console-trust-note"><ShieldCheck size={15} /><span>{t('业务数据不落监控库；每次请求由 New API Session 再鉴权。')}</span></div>
      </aside>
      <main className="console-main">
        <div className="console-page-heading"><div><span className="eyebrow">{current.id.toUpperCase()}</span><h2>{current.label}</h2><p>{current.detail}</p></div></div>
        {activePage === 'overview' && <ConsoleOverview onNavigate={onNavigate} pages={pages} />}
        {activePage === 'analytics' && <ConsoleAnalytics globalScope={globalScope} />}
        {activePage === 'keys' && <ConsoleKeys />}
        {activePage === 'logs' && <ConsoleLogs globalScope={globalScope} />}
      </main>
    </div>
  </section>
}
