import { describe, expect, test } from 'bun:test'
import { buildProviderStatusContext } from './provider-status'
import type { ProviderStatus } from './types'

function status(overrides: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider: 'openai',
    available: true,
    stale: false,
    observed_at: 100,
    indicator: 'none',
    description: 'All Systems Operational',
    components: [
      { id: 'responses', name: 'Responses', status: 'operational' },
      { id: 'unrelated', name: 'Sora', status: 'operational' },
    ],
    incidents: [],
    active_incident_count: 0,
    degraded_component_count: 0,
    monitored_component_ids: ['responses'],
    ...overrides,
  }
}

describe('provider status presentation', () => {
  test('treats unrelated official incidents as context instead of a local outage', () => {
    const context = buildProviderStatusContext(status({
      indicator: 'minor',
      description: 'Minor Service Outage',
      active_incident_count: 1,
      components: [
        { id: 'responses', name: 'Responses', status: 'operational' },
        { id: 'unrelated', name: 'Sora', status: 'partial_outage' },
      ],
    }))

    expect(context.state).toBe('global-notice')
    expect(context.relevantComponents).toHaveLength(1)
    expect(context.relevantDegradedComponents).toHaveLength(0)
  })

  test('raises attention only when a monitored business component degrades', () => {
    const context = buildProviderStatusContext(status({
      indicator: 'major',
      components: [
        { id: 'responses', name: 'Responses', status: 'major_outage' },
        { id: 'unrelated', name: 'Sora', status: 'operational' },
      ],
    }))

    expect(context.state).toBe('relevant-issue')
    expect(context.relevantDegradedComponents.map((component) => component.id)).toEqual(['responses'])
  })

  test('uses recommended API components when no explicit scope is configured', () => {
    const context = buildProviderStatusContext(status({
      monitored_component_ids: [],
      components: [
        { id: 'responses', name: 'Responses', status: 'operational' },
        { id: 'chat', name: 'Chat Completions', status: 'operational' },
        { id: 'sora', name: 'Sora', status: 'major_outage' },
      ],
    }))

    expect(context.state).toBe('global-notice')
    expect(context.relevantComponents.map((component) => component.id)).toEqual(['responses', 'chat'])
  })
})
