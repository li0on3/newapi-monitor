import { Monitor, MoonStar, Sun } from 'lucide-react'
import { t } from './i18n'
import { useTheme } from './ThemeProvider'
import type { ThemePreference } from './theme'

const OPTIONS: Array<{ value: ThemePreference; label: string; icon: typeof Monitor }> = [
  { value: 'system', label: '跟随系统', icon: Monitor },
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'dark', label: '深色', icon: MoonStar },
]

export function ThemeSwitch({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useTheme()

  return (
    <div className={`theme-switcher${compact ? ' theme-switcher-compact' : ''}`} role="radiogroup" aria-label={t('主题')}>
      {OPTIONS.map((option) => {
        const Icon = option.icon
        return (
          <button
            type="button"
            role="radio"
            aria-checked={theme === option.value}
            aria-label={t(option.label)}
            title={t(option.label)}
            className={theme === option.value ? 'active' : ''}
            key={option.value}
            onClick={() => setTheme(option.value)}
          >
            <Icon size={14} />
            {!compact && <span>{t(option.label)}</span>}
          </button>
        )
      })}
    </div>
  )
}
