export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

export const THEME_STORAGE_KEY = 'newapi-monitor-theme'

const THEMES = new Set<ThemePreference>(['light', 'dark', 'system'])

export function normalizeTheme(value: string | null | undefined): ThemePreference {
  return value && THEMES.has(value as ThemePreference) ? value as ThemePreference : 'system'
}

export function resolveTheme(theme: ThemePreference, prefersDark: boolean): ResolvedTheme {
  return theme === 'system' ? (prefersDark ? 'dark' : 'light') : theme
}
