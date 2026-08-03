import { describe, expect, test } from 'bun:test'
import { buildAnalyticsFlowRows, buildAnalyticsModelTimeline, buildAnalyticsTimeline, summarizeFlowOverflow, logsToCsv } from './utils'

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

  test('builds a top-model trend and merges overflow models without losing totals', () => {
    const result = buildAnalyticsModelTimeline([
      { created_at: 100, username: '', model_name: 'gpt-a', count: 8, quota: 80, token_used: 40 },
      { created_at: 100, username: '', model_name: 'gpt-b', count: 5, quota: 50, token_used: 25 },
      { created_at: 100, username: '', model_name: 'gpt-c', count: 2, quota: 20, token_used: 10 },
      { created_at: 200, username: '', model_name: 'gpt-c', count: 10, quota: 100, token_used: 50 },
    ], 'requests', 2)

    expect(result.models.slice(0, 2)).toEqual(['gpt-c', 'gpt-a'])
    expect(result.otherModelKey).toBe(result.models[2])
    expect(result.points).toEqual([
      { timestamp: 100, total: 15, values: { 'gpt-c': 2, 'gpt-a': 8, [result.otherModelKey!]: 5 } },
      { timestamp: 200, total: 10, values: { 'gpt-c': 10, 'gpt-a': 0, [result.otherModelKey!]: 0 } },
    ])
    expect(result.peak).toBe(15)
    expect(result.average).toBe(12.5)
  })

  test('keeps a real model named 其他模型 separate from the overflow bucket', () => {
    const result = buildAnalyticsModelTimeline([
      { created_at: 100, username: '', model_name: '其他模型', count: 10, quota: 10, token_used: 10 },
      { created_at: 100, username: '', model_name: 'gpt-a', count: 5, quota: 5, token_used: 5 },
      { created_at: 100, username: '', model_name: 'gpt-b', count: 4, quota: 4, token_used: 4 },
    ], 'requests', 2)

    expect(result.otherModelKey).not.toBe('其他模型')
    expect(result.models).toContain('其他模型')
    expect(result.points[0].values['其他模型']).toBe(10)
    expect(result.points[0].values[result.otherModelKey!]).toBe(4)
    expect(result.points[0].total).toBe(19)
  })

  test('merges flow rows split only by hidden channel and node dimensions', () => {
    const result = buildAnalyticsFlowRows([
      { username: 'alice', node_name: 'node-a', token_id: 7, token_name: 'main', use_group: 'default', channel_id: 1, channel_name: 'one', model_name: 'gpt-5.4', token_used: 10, count: 1, quota: 30 },
      { username: 'alice', node_name: 'node-b', token_id: 7, token_name: 'main', use_group: 'default', channel_id: 2, channel_name: 'two', model_name: 'gpt-5.4', token_used: 20, count: 2, quota: 70 },
      { username: 'bob', node_name: 'node-a', token_id: 8, token_name: 'backup', use_group: 'default', channel_id: 1, channel_name: 'one', model_name: 'gpt-5.5', token_used: 5, count: 1, quota: 10 },
    ])

    expect(result).toEqual([
      { key: 'token:7\u0000default\u0000gpt-5.4', username: 'alice', token_id: 7, token_name: 'main', use_group: 'default', model_name: 'gpt-5.4', token_used: 30, count: 3, quota: 100 },
      { key: 'token:8\u0000default\u0000gpt-5.5', username: 'bob', token_id: 8, token_name: 'backup', use_group: 'default', model_name: 'gpt-5.5', token_used: 5, count: 1, quota: 10 },
    ])
  })

  test('summarizes hidden flow rows without losing quota or requests', () => {
    const rows = Array.from({ length: 14 }, (_, index) => ({
      key: `token:${index}`,
      username: 'alice',
      token_id: index,
      token_name: `key-${index}`,
      use_group: 'default',
      model_name: 'gpt-5.4',
      token_used: index + 1,
      count: index + 2,
      quota: 100 - index,
    }))

    expect(summarizeFlowOverflow(rows, 12)).toEqual({
      count: 2,
      token_used: 27,
      requests: 29,
      quota: 175,
    })
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
