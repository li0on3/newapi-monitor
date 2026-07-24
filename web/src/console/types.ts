export type ConsolePageKey = 'overview' | 'analytics' | 'keys' | 'logs'

export type ConsoleCapabilities = {
  available: boolean
  pages: Partial<Record<ConsolePageKey, boolean>>
  global_scope: boolean
}

export type ConsoleToken = {
  id: number
  name: string
  masked_key: string
  status: number
  created_time: number
  accessed_time: number
  expired_time: number
  remain_quota: number
  used_quota: number
  unlimited_quota: boolean
  model_limits_enabled: boolean
  model_limits: string
  allow_ips: string
  group: string
  cross_group_retry: boolean
}

export type ConsoleTokenDraft = Omit<ConsoleToken, 'id' | 'masked_key' | 'status' | 'created_time' | 'accessed_time' | 'used_quota'>

export type ConsoleTokenPage = {
  page: number
  page_size: number
  total: number
  items: ConsoleToken[]
  quota_per_unit?: number
}

export type ConsoleOverview = {
  generated_at: number
  scope: 'self' | 'global'
  system: {
    version: string
    system_name: string
    server_address: string
    docs_link: string
    quota_per_unit: number
    quota_display_type: string
  }
  user: {
    id: number
    username: string
    display_name: string
    role: number
    status: number
    group: string
    quota: number
    used_quota: number
    request_count: number
  }
  models: { total: number; items: string[] }
  keys: ConsoleTokenPage
  usage_24h: { quota: number; rpm: number; tpm: number }
}

export type ConsoleSeriesItem = {
  created_at: number
  username: string
  model_name: string
  count: number
  quota: number
  token_used: number
}

export type ConsoleFlowItem = {
  username: string
  node_name: string
  token_id: number
  token_name: string
  use_group: string
  channel_id: number
  channel_name: string
  model_name: string
  token_used: number
  count: number
  quota: number
}

export type ConsoleAnalytics = {
  start_timestamp: number
  end_timestamp: number
  scope: 'self' | 'global'
  series: ConsoleSeriesItem[]
  flow: ConsoleFlowItem[]
  stat: { quota: number; rpm: number; tpm: number }
  summary: { requests: number; quota: number; tokens: number; models: number }
  quota_per_unit: number
}

export type ConsoleLog = {
  id: number
  created_at: number
  type: number
  content: string
  username: string
  token_name: string
  model_name: string
  quota: number
  prompt_tokens: number
  completion_tokens: number
  use_time: number
  is_stream: boolean
  channel_id: number
  channel_name: string
  group: string
  request_id: string
  upstream_request_id: string
  other: Record<string, unknown>
}

export type ConsoleLogPage = {
  page: number
  page_size: number
  total: number
  items: ConsoleLog[]
  stat: { quota: number; rpm: number; tpm: number } | null
  stat_filters_complete: boolean
  quota_per_unit: number
  scope: 'self' | 'global'
}
