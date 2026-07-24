import { describe, expect, test } from 'bun:test'
import { readRoute } from './routes'

describe('dashboard routes', () => {
  test('routes upstream status to its own page', () => {
    expect(readRoute('/monitor/upstream-status')).toEqual({
      tab: 'providerStatus',
      settingsPage: 'status',
      consolePage: 'overview',
    })
  })

  test('routes provider settings to a stable deep link', () => {
    expect(readRoute('/monitor/system/providers')).toEqual({
      tab: 'settings',
      settingsPage: 'providers',
      consolePage: 'overview',
    })
  })

  test('routes each customer console page to a stable deep link', () => {
    expect(readRoute('/monitor/console')).toEqual({
      tab: 'console',
      settingsPage: 'status',
      consolePage: 'overview',
    })
    expect(readRoute('/monitor/console/analytics')).toEqual({
      tab: 'console',
      settingsPage: 'status',
      consolePage: 'analytics',
    })
    expect(readRoute('/monitor/console/keys')).toEqual({
      tab: 'console',
      settingsPage: 'status',
      consolePage: 'keys',
    })
    expect(readRoute('/monitor/console/logs')).toEqual({
      tab: 'console',
      settingsPage: 'status',
      consolePage: 'logs',
    })
  })
})
