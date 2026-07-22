import type { ProviderStatus, ProviderStatusComponent } from './types'

export const DEFAULT_OPENAI_COMPONENT_NAMES = new Set(['Responses', 'Chat Completions', 'Codex API', 'CLI'])

export type ProviderStatusContextState = 'stale' | 'relevant-issue' | 'global-notice' | 'operational'

export type ProviderStatusContext = {
  state: ProviderStatusContextState
  relevantComponents: ProviderStatusComponent[]
  relevantDegradedComponents: ProviderStatusComponent[]
}

export function buildProviderStatusContext(status: ProviderStatus): ProviderStatusContext {
  const monitoredIds = new Set(status.monitored_component_ids || [])
  const relevantComponents = status.components.filter((component) => monitoredIds.size
    ? monitoredIds.has(component.id)
    : DEFAULT_OPENAI_COMPONENT_NAMES.has(component.name))
  const relevantDegradedComponents = relevantComponents.filter((component) => component.status !== 'operational')
  const hasGlobalNotice = status.indicator !== 'none'
    || status.active_incident_count > 0
    || status.components.some((component) => component.status !== 'operational')

  const state: ProviderStatusContextState = status.stale || !status.available
    ? 'stale'
    : relevantDegradedComponents.length
      ? 'relevant-issue'
      : hasGlobalNotice
        ? 'global-notice'
        : 'operational'

  return { state, relevantComponents, relevantDegradedComponents }
}
