import type { Channel, Observation, Summary } from './types'

export type ChannelHealthState = 'unknown' | 'stale' | 'failed' | 'delayed' | 'healthy'
export type OverallHealthState = 'syncing' | 'critical' | 'warning' | 'healthy'

export function observationHealth(observation: Observation, slowAfterSeconds: number) {
  if (!observation.success) return { state: 'failed' as const, tone: 'bad' as const }
  const slowAfterMs = Math.max(1, slowAfterSeconds || 30) * 1000
  if (observation.elapsed_ms > slowAfterMs || (observation.frt_ms || 0) > slowAfterMs) {
    return { state: 'delayed' as const, tone: 'warn' as const }
  }
  return { state: 'healthy' as const, tone: 'ok' as const }
}

export function channelHealth(channel: Channel, nowSeconds = Date.now() / 1000) {
  const latest = channel.latest
  if (!latest) return { state: 'unknown' as const, tone: 'muted' as const }
  if (nowSeconds - latest.observed_at > Math.max(60, channel.stale_after_seconds || 900)) {
    return { state: 'stale' as const, tone: 'muted' as const }
  }
  return observationHealth(latest, channel.slow_after_seconds)
}

export function overallHealth(summary: Summary | null, providerIssue = false) {
  if (!summary) return { state: 'syncing' as const, reason: 'syncing' as const, tone: 'muted' as const }
  if (summary.channel_sync?.status === 'stale') {
    return { state: 'critical' as const, reason: 'channel-sync-stale' as const, tone: 'bad' as const }
  }
  if (summary.channels.failed > 0) {
    return { state: 'critical' as const, reason: 'failed-channels' as const, tone: 'bad' as const }
  }
  if (summary.incidents.critical > 0) {
    return { state: 'critical' as const, reason: 'critical-incidents' as const, tone: 'bad' as const }
  }
  if (providerIssue) {
    return { state: 'critical' as const, reason: 'provider-issue' as const, tone: 'bad' as const }
  }
  if ((summary.channels.delayed || 0) > 0) {
    return { state: 'warning' as const, reason: 'delayed-channels' as const, tone: 'warn' as const }
  }
  if ((summary.incidents.warning || 0) > 0) {
    return { state: 'warning' as const, reason: 'warning-incidents' as const, tone: 'warn' as const }
  }
  if (summary.requests.collector_status === 'stale') {
    return { state: 'warning' as const, reason: 'log-collector-stale' as const, tone: 'warn' as const }
  }
  if (summary.resources.collector_status === 'stale') {
    return { state: 'warning' as const, reason: 'resource-collector-stale' as const, tone: 'warn' as const }
  }
  if (summary.channels.unknown > 0) {
    return { state: 'warning' as const, reason: 'unknown-channels' as const, tone: 'warn' as const }
  }
  return { state: 'healthy' as const, reason: 'healthy' as const, tone: 'ok' as const }
}
