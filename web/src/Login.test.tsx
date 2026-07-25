import { describe, expect, test } from 'bun:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { Login } from './App'
import { ThemeProvider } from './ThemeProvider'

describe('Login', () => {
  test('uses neutral customer-facing identity wording before the user role is known', () => {
    const html = renderToStaticMarkup(<ThemeProvider><Login onSuccess={() => undefined} /></ThemeProvider>)

    expect(html).toContain('账号登录')
    expect(html).toContain('服务状态、用量与访问凭据')
    expect(html).not.toContain('New API')
  })

  test('does not automatically redirect an unauthenticated user to a branded upstream login', () => {
    const source = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8')

    expect(source).not.toContain("window.location.assign('/login')")
    expect(source).toContain('当前浏览器没有有效账号会话，请先登录账户服务后重试。')
  })
})
