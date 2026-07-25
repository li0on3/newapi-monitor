import { describe, expect, test } from 'bun:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { ConsoleShell } from './ConsoleShell'

describe('ConsoleShell', () => {
  test('renders a page as a top-level module without a nested customer console shell', () => {
    const html = renderToStaticMarkup(
      <ConsoleShell
        page="analytics"
        pages={{ overview: true, analytics: true, keys: true, logs: true }}
        globalScope={false}
        onNavigate={() => undefined}
      />,
    )

    expect(html).toContain('数据看板')
    expect(html).toContain('请求、Token 与额度趋势')
    expect(html).not.toContain('New API 客户控制台')
    expect(html).not.toContain('客户控制台导航')
  })
})
