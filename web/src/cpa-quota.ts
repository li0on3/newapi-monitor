export type CpaQuotaTone = 'healthy' | 'warning' | 'critical'

export function quotaTone(
  remainingPercent: number,
  alertThresholdPercent: number,
  recoveryThresholdPercent: number,
): CpaQuotaTone {
  if (remainingPercent <= alertThresholdPercent) return 'critical'
  if (remainingPercent <= recoveryThresholdPercent) return 'warning'
  return 'healthy'
}

export function quotaWindowLabel(value: string): string {
  return value.trim() || 'Weekly quota'
}
