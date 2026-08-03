import { describe, expect, test } from 'bun:test'
import type { Channel, Summary } from './types'
import { channelHealth, observationHealth, overallHealth } from './monitor-status'

function channel(overrides: Partial<Channel> = {}): Channel {
  return {
    channel_id: 1,
    name: 'channel',
    channel_type: 1,
    enabled: true,
    raw_status: 1,
    models: ['gpt'],
    group: 'default',
    synced_at: 1_000,
    stale_after_seconds: 900,
    slow_after_seconds: 30,
    latest: {
      observed_at: 1_000,
      success: true,
      elapsed_ms: 1_000,
      frt_ms: 500,
      message: '',
      source: 'real',
    },
    history: [],
    availability: {
      window_seconds: 3600,
      start_timestamp: 0,
      end_timestamp: 1_000,
      all_time: false,
      source: 'real',
      coverage_start_at: 1_000,
      coverage_end_at: 1_000,
      retention_days: 90,
      total: 1,
      successes: 1,
      percentage: 100,
    },
    usage_24h: { requests: 0, slow: 0, average_seconds: 0, p95_seconds: 0, last_request_at: 0 },
    ...overrides,
  }
}

function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    generated_at: 2_000,
    channel_sync: { status: 'ok', age_seconds: 0 },
    channels: { total: 1, healthy: 1, delayed: 0, failed: 0, unknown: 0, last_checked_at: 2_000 },
    requests: { window_seconds: 86400, total: 1, slow: 0, slow_after_seconds: 60, slow_ratio: 0, average_seconds: 1, p95_seconds: 1, average_frt_ms: 500, last_request_at: 2_000, collector_status: 'ok', collector_age_seconds: 0, collector_stale_after_seconds: 120, last_collected_at: 2_000 },
    resources: { created_at: 2_000, system_cpu: 10, system_memory: 20, system_disk: 30, system_available_mb: 1000, system_swap: 0, collector_status: 'ok', collector_age_seconds: 0, collector_stale_after_seconds: 90, last_collected_at: 2_000 },
    incidents: { open: 0, critical: 0, warning: 0 },
    ...overrides,
  }
}

describe('channelHealth', () => {
  test('uses the backend staleness threshold instead of a hard-coded UI timeout', () => {
    expect(channelHealth(channel(), 1_899).state).toBe('healthy')
    expect(channelHealth(channel(), 1_901).state).toBe('stale')
  })

  test('distinguishes missing samples from failed samples', () => {
    expect(channelHealth(channel({ latest: null }), 2_000).state).toBe('unknown')
    expect(channelHealth(channel({ latest: { ...channel().latest!, success: false } }), 1_100).state).toBe('failed')
  })

  test('classifies every history sample with the channel dynamic slow threshold', () => {
    const point = { ...channel().latest!, elapsed_ms: 35_000, frt_ms: 5_000 }
    expect(observationHealth(point, 60).state).toBe('healthy')
    expect(observationHealth(point, 30).state).toBe('delayed')
  })
})

describe('overallHealth', () => {
  test('does not downgrade overall health for an isolated display-only slow request', () => {
    const result = overallHealth(summary({ requests: { ...summary().requests, slow: 1 } }))
    expect(result.state).toBe('healthy')
  })

  test('uses warning incidents and unknown channels as actionable warning reasons', () => {
    expect(overallHealth(summary({ incidents: { open: 1, critical: 0, warning: 1 } })).state).toBe('warning')
    expect(overallHealth(summary({ channels: { ...summary().channels, healthy: 0, unknown: 1 } })).reason).toBe('unknown-channels')
  })

  test('keeps delayed-channel totals consistent with delayed channel cards', () => {
    const result = overallHealth(summary({ channels: { ...summary().channels, healthy: 0, delayed: 1 } }))
    expect(result.reason).toBe('delayed-channels')
  })

  test('surfaces stale visible collectors without claiming a channel outage', () => {
    expect(overallHealth(summary({ requests: { ...summary().requests, collector_status: 'stale' } })).reason).toBe('log-collector-stale')
    expect(overallHealth(summary({ resources: { ...summary().resources, collector_status: 'stale' } })).reason).toBe('resource-collector-stale')
  })
})
