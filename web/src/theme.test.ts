import { describe, expect, test } from 'bun:test'
import { normalizeTheme, resolveTheme } from './theme'

describe('theme preference', () => {
  test('accepts supported values and falls back to system', () => {
    expect(normalizeTheme('light')).toBe('light')
    expect(normalizeTheme('dark')).toBe('dark')
    expect(normalizeTheme('system')).toBe('system')
    expect(normalizeTheme('unknown')).toBe('system')
    expect(normalizeTheme(null)).toBe('system')
  })

  test('resolves system preference without overriding explicit choices', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  test('loads the pre-render theme bootstrap as a CSP-compatible external script', async () => {
    const html = await Bun.file(new URL('../index.html', import.meta.url)).text()

    expect(html).toContain('<script src="/theme-init.js"></script>')
    expect(html).not.toContain("localStorage.getItem('newapi-monitor-theme')")
  })
})
