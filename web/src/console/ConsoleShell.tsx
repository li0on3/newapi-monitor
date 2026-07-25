import { ShieldCheck } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { t } from '../i18n'
import { enabledConsolePages } from '../routes'
import type { ConsolePage } from '../routes'
import { ConsoleAnalytics } from './ConsoleAnalytics'
import { ConsoleKeys } from './ConsoleKeys'
import { ConsoleLogs } from './ConsoleLogs'
import { ConsoleOverview } from './ConsoleOverview'
import type { ConsolePageKey } from './types'

const ITEMS = [
  { id: 'overview' as const, label: t('概览'), detail: t('账号、额度与快捷入口') },
  { id: 'analytics' as const, label: t('数据看板'), detail: t('请求、Token 与额度趋势') },
  { id: 'keys' as const, label: t('API 密钥'), detail: t('创建与管理客户端凭据') },
  { id: 'logs' as const, label: t('使用日志'), detail: t('检索真实调用详情') },
]

export function ConsoleShell({
  page,
  pages,
  globalScope,
  customerView,
  onNavigate,
}: {
  page: ConsolePage
  pages: Partial<Record<ConsolePageKey, boolean>>
  globalScope: boolean
  customerView: boolean
  onNavigate: (page: ConsolePage) => void
}) {
  const visiblePageIds = useMemo(() => enabledConsolePages(pages), [pages])
  const visible = useMemo(() => ITEMS.filter((item) => visiblePageIds.includes(item.id)), [visiblePageIds])
  const activePage = visible.some((item) => item.id === page) ? page : (visible[0]?.id || 'overview')
  useEffect(() => {
    if (activePage !== page) onNavigate(activePage)
  }, [activePage, onNavigate, page])
  const current = ITEMS.find((item) => item.id === activePage) || ITEMS[0]

  return <section className="console-workspace console-workspace-flat">
    <header className="console-module-header">
      <div><span className="eyebrow">{customerView ? 'SERVICE' : 'NEW API'} / {current.id.toUpperCase()}</span><h1>{current.label}</h1><p>{current.detail}</p></div>
      <div className="console-scope-chip"><ShieldCheck size={17} /><span><strong>{globalScope ? t('管理员数据范围') : t('个人数据范围')}</strong><small>{customerView ? t('账号权限实时校验') : t('权限始终由 New API 校验')}</small></span></div>
    </header>
    <main className="console-main console-main-flat">
      {activePage === 'overview' && <ConsoleOverview onNavigate={onNavigate} pages={pages} />}
      {activePage === 'analytics' && <ConsoleAnalytics globalScope={globalScope} />}
      {activePage === 'keys' && <ConsoleKeys />}
      {activePage === 'logs' && <ConsoleLogs globalScope={globalScope} />}
    </main>
  </section>
}
