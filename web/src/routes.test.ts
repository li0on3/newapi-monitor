import { describe, expect, test } from 'bun:test'
import { readRoute } from './routes'

describe('dashboard routes', () => {
  test('routes provider settings to a stable deep link', () => {
    expect(readRoute('/monitor/system/providers')).toEqual({
      tab: 'settings',
      settingsPage: 'providers',
    })
  })
})
