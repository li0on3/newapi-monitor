import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { t } from '../i18n'

export function ConsoleLoading({ label = t('正在读取 New API 数据') }: { label?: string }) {
  return <div className="console-state"><LoaderCircle className="spin" size={24} /><strong>{label}</strong></div>
}

export function ConsoleError({ message, retry }: { message: string; retry: () => void }) {
  return <div className="console-state console-error"><AlertTriangle size={24} /><strong>{t('数据读取失败')}</strong><span>{message}</span><button className="secondary-button" type="button" onClick={retry}><RefreshCw size={15} />{t('重试')}</button></div>
}

export function ConsoleMetric({ icon, label, value, detail, tone = 'neutral' }: { icon: ReactNode; label: string; value: string; detail: string; tone?: 'neutral' | 'green' | 'amber' | 'blue' }) {
  return <article className={`console-metric console-metric-${tone}`}><span className="console-metric-icon">{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{detail}</p></div></article>
}

export function ConsoleBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' }) {
  return <span className={`console-badge console-badge-${tone}`}>{children}</span>
}

export function ConsoleEmpty({ title, detail }: { title: string; detail: string }) {
  return <div className="console-state console-empty"><strong>{title}</strong><span>{detail}</span></div>
}
