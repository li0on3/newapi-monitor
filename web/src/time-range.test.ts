import { describe, expect, test } from 'bun:test'
import { allTimeRange, dateRangeQuery, isLiveRange, presetRange } from './time-range'

describe('time ranges', () => {
  test('builds inclusive date query parameters', () => {
    expect(dateRangeQuery({ mode: 'custom', startDate: '2026-07-01', endDate: '2026-07-27' })).toEqual({
      start_timestamp: 1782864000,
      end_timestamp: 1785196799,
    })
  })

  test('represents creation-to-now without fake dates', () => {
    expect(dateRangeQuery(allTimeRange())).toEqual({ all_time: 1 })
  })

  test('creates quick presets ending today', () => {
    expect(presetRange(7, new Date('2026-07-27T12:00:00+08:00'))).toEqual({
      mode: 'custom', startDate: '2026-07-21', endDate: '2026-07-27', label: '7d',
    })
  })

  test('refreshes live ranges but leaves closed historical ranges stable', () => {
    const now = new Date('2026-07-27T12:00:00+08:00')
    expect(isLiveRange(allTimeRange(), now)).toBe(true)
    expect(isLiveRange(presetRange(7, now), now)).toBe(true)
    expect(isLiveRange({ mode: 'custom', startDate: '2026-06-01', endDate: '2026-06-30' }, now)).toBe(false)
  })
})
