import { describe, expect, test } from 'bun:test'
import * as routes from './routes'

const { readRoute } = routes

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

  test('returns enabled New API pages in stable top-level navigation order', () => {
    const enabledConsolePages = (
      routes as typeof routes & {
        enabledConsolePages?: (
          pages: Partial<Record<routes.ConsolePage, boolean>>,
        ) => routes.ConsolePage[]
      }
    ).enabledConsolePages

    expect(typeof enabledConsolePages).toBe('function')
    expect(enabledConsolePages?.({ overview: true, analytics: true, keys: false, logs: true })).toEqual([
      'overview',
      'analytics',
      'logs',
    ])
  })

  test('regular users fall back to New API pages and cannot access monitor modules', () => {
    const access = routes as typeof routes & {
      canAccessMonitorModules?: (role: string) => boolean
      defaultAuthorizedRoute?: (
        role: string,
        pages: Partial<Record<routes.ConsolePage, boolean>>,
      ) => routes.AppRoute
    }

    expect(typeof access.canAccessMonitorModules).toBe('function')
    expect(access.canAccessMonitorModules?.('viewer')).toBe(false)
    expect(access.canAccessMonitorModules?.('operator')).toBe(true)
    expect(access.canAccessMonitorModules?.('admin')).toBe(true)
    expect(access.defaultAuthorizedRoute?.('viewer', {
      overview: true,
      analytics: true,
      keys: true,
      logs: true,
    })).toEqual({
      tab: 'console',
      settingsPage: 'status',
      consolePage: 'overview',
    })
    expect(access.defaultAuthorizedRoute?.('viewer', {
      overview: false,
      analytics: true,
      keys: true,
      logs: true,
    })?.consolePage).toBe('analytics')
  })
})
