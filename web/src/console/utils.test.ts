import { describe, expect, test } from 'bun:test'
import { buildAnalyticsTimeline, logsToCsv } from './utils'

describe('customer console analytics utilities', () => {
  test('groups duplicate time buckets without losing request or token totals', () => {
    expect(buildAnalyticsTimeline([
      { created_at: 100, username: '', model_name: 'gpt-5.4', count: 2, quota: 50, token_used: 20 },
      { created_at: 100, username: '', model_name: 'gpt-5.5', count: 3, quota: 70, token_used: 30 },
      { created_at: 200, username: '', model_name: 'gpt-5.4', count: 1, quota: 10, token_used: 5 },
    ])).toEqual([
      { timestamp: 100, requests: 5, quota: 120, tokens: 50 },
      { timestamp: 200, requests: 1, quota: 10, tokens: 5 },
    ])
  })

  test('exports logs as safe CSV with quotes and newlines escaped', () => {
    const csv = logsToCsv([{
      id: 1,
      created_at: 100,
      type: 2,
      content: 'line one\n"line two"',
      username: 'alice',
      token_name: 'main',
      model_name: 'gpt-5.4',
      quota: 50,
      prompt_tokens: 10,
      completion_tokens: 5,
      use_time: 3,
      is_stream: true,
      channel_id: 7,
      channel_name: '',
      group: 'default',
      request_id: 'req-1',
      upstream_request_id: 'up-1',
      other: {},
    }])

    expect(csv).toContain('"line one\n""line two"""')
    expect(csv.split('\n')[0]).toContain('request_id')
  })

  test('neutralizes spreadsheet formulas in exported log cells', () => {
    const csv = logsToCsv([{
      id: 1,
      created_at: 100,
      type: 2,
      content: '=HYPERLINK("https://example.invalid")',
      username: '+cmd',
      token_name: '-formula',
      model_name: '@SUM(A1:A2)',
      quota: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      use_time: 0,
      is_stream: false,
      channel_id: 0,
      channel_name: '',
      group: 'default',
      request_id: 'req-1',
      upstream_request_id: '',
      other: {},
    }])

    expect(csv).toContain('"\'=HYPERLINK(""https://example.invalid"")"')
    expect(csv).toContain('"\'+cmd"')
    expect(csv).toContain('"\'-formula"')
    expect(csv).toContain('"\'@SUM(A1:A2)"')
  })
})
