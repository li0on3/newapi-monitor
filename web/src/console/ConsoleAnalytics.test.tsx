import { describe, expect, test } from 'bun:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { ConsoleAnalytics } from './ConsoleAnalytics'

describe('ConsoleAnalytics', () => {
  test('makes the administrator analytics scope explicit', () => {
    const html = renderToStaticMarkup(<ConsoleAnalytics globalScope />)

    expect(html).toContain('analytics-scope-tabs')
    expect(html).toContain('全局')
    expect(html).toContain('当前账号')
  })
})
