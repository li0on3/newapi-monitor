import { describe, expect, test } from 'bun:test'
import { readRoute } from './routes'

describe('dashboard routes', () => {
  test('routes upstream status to its own page', () => {
    expect(readRoute('/monitor/upstream-status')).toEqual({
      tab: 'providerStatus',
      settingsPage: 'status',
    })
  })

  test('routes provider settings to a stable deep link', () => {
    expect(readRoute('/monitor/system/providers')).toEqual({
      tab: 'settings',
      settingsPage: 'providers',
    })
  })
})
