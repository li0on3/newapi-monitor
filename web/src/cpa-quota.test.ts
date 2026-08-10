import { describe, expect, test } from 'bun:test'
import { quotaTone, quotaWindowLabel } from './cpa-quota'

describe('CPA quota presentation', () => {
  test('uses the configured threshold instead of a hard-coded status boundary', () => {
    expect(quotaTone(18, 20, 25)).toBe('critical')
    expect(quotaTone(22, 20, 25)).toBe('warning')
    expect(quotaTone(26, 20, 25)).toBe('healthy')
    expect(quotaTone(18, 15, 16)).toBe('healthy')
  })

  test('keeps provider window names human readable', () => {
    expect(quotaWindowLabel('Main weekly quota')).toBe('Main weekly quota')
    expect(quotaWindowLabel('')).toBe('Weekly quota')
  })
})
