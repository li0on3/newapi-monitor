import { describe, expect, test } from 'bun:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { AnalyticsTrendChart } from './AnalyticsTrendChart'

describe('AnalyticsTrendChart', () => {
  test('renders a visible mark and accessible table for a single time bucket', () => {
    const html = renderToStaticMarkup(<AnalyticsTrendChart
      quotaPerUnit={500000}
      series={[{
        created_at: 100,
        username: 'alice',
        model_name: 'gpt-a',
        count: 3,
        quota: 100,
        token_used: 20,
      }]}
    />)

    expect(html).toContain('analytics-single-point')
    expect(html).toContain('<table class="sr-only">')
    expect(html).toContain('趋势数据表')
  })
})
