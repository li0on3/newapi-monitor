export type Observation = {
  observed_at: number;
  success: boolean;
  elapsed_ms: number;
  frt_ms: number | null;
  message: string;
  source: 'real' | 'builtin' | string;
};

export type Channel = {
  channel_id: number;
  name: string;
  channel_type: number;
  enabled: boolean;
  raw_status: number;
  models: string[];
  group: string;
  synced_at: number;
  latest: Observation | null;
  history: Observation[];
  availability: {
    window_seconds: number;
    total: number;
    successes: number;
    percentage: number | null;
  };
  usage_24h: {
    requests: number;
    slow: number;
    average_seconds: number;
    p95_seconds: number;
    last_request_at: number;
  };
  source_name?: string;
  display_enabled?: boolean;
  overview_admin_visible?: boolean;
  overview_viewer_visible?: boolean;
  monitor_config?: ChannelMonitorConfig;
};

export type ChannelMonitorConfig = {
  display_enabled?: boolean;
  overview_admin_visible?: boolean;
  overview_viewer_visible?: boolean;
  display_name?: string;
  sort_order?: number;
  probe_enabled?: boolean;
  probe_model?: string;
  probe_path?: string;
  probe_format?: 'responses' | 'chat' | 'anthropic';
  probe_prompt?: string;
  max_output_tokens?: number;
  alert_enabled?: boolean;
  maintenance_mode?: boolean;
};

export type AuthUser = {
  authenticated: boolean;
  username: string;
  display_name?: string;
  role: 'viewer' | 'operator' | 'admin';
  source: 'newapi' | 'emergency';
  source_role?: number;
  dashboard_refresh_seconds?: number;
  key_usage_available?: boolean;
};

export type KeyUsageCall = {
  id: number;
  created_at: number;
  type: number;
  model_name: string;
  quota: number;
  quota_display: number;
  prompt_tokens: number;
  completion_tokens: number;
  use_time: number;
  frt_ms: number | null;
  is_stream: boolean;
  channel_id: number;
  channel_name: string;
  request_id: string;
  upstream_request_id: string;
  group: string;
  content: string;
};

export type KeyUsageResult = {
  queried_at: number;
  quota_per_unit: number;
  usage: {
    name: string;
    total_granted: number;
    total_used: number;
    total_available: number;
    total_granted_display: number;
    total_used_display: number;
    total_available_display: number;
    used_percentage: number | null;
    unlimited_quota: boolean;
    expires_at: number;
    model_limits_enabled: boolean;
    model_limits: Record<string, boolean>;
  };
  summary: {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    quota: number;
    quota_display: number;
    average_seconds: number;
    p95_seconds: number;
    models: Array<{ name: string; calls: number }>;
  };
  calls: KeyUsageCall[];
};

export type Summary = {
  generated_at: number;
  channels: {
    total: number;
    healthy: number;
    failed: number;
    unknown: number;
    last_checked_at: number;
  };
  requests: {
    window_seconds: number;
    total: number;
    slow: number;
    slow_ratio: number;
    average_seconds: number;
    p95_seconds: number;
    average_frt_ms: number | null;
    last_request_at: number;
  };
  resources: ResourceSample & { containers?: Record<string, ContainerMetric> };
  incidents: { open: number; critical: number };
  provider_status?: ProviderStatus;
};

export type ProviderStatusComponent = {
  id: string;
  name: string;
  status: string;
  updated_at?: number;
};

export type ProviderStatusUpdate = {
  id?: string;
  status: string;
  body: string;
  created_at: number;
  updated_at?: number;
};

export type ProviderStatusIncident = {
  id: string;
  name: string;
  status: string;
  impact: string;
  created_at: number;
  updated_at: number;
  latest_update?: ProviderStatusUpdate;
  updates?: ProviderStatusUpdate[];
};

export type ProviderStatus = {
  provider: string;
  enabled?: boolean;
  available: boolean;
  stale: boolean;
  observed_at: number;
  age_seconds?: number;
  indicator: string;
  description: string;
  source_url?: string;
  components: ProviderStatusComponent[];
  incidents: ProviderStatusIncident[];
  active_incident_count: number;
  degraded_component_count: number;
  include_in_overall?: boolean;
  monitored_component_ids?: string[];
};

export type LogItem = {
  created_at: number;
  channel_id: number;
  channel_name: string;
  model_name: string;
  use_time: number;
  frt_ms: number | null;
  username: string;
  token_name: string;
  token_id: number;
  is_stream: number;
  request_id: string;
  upstream_request_id: string;
  group_name: string;
};

export type ContainerMetric = {
  status: string;
  restarts: number;
  cpu: number;
  memory: number;
  memory_mb: number;
  oom_killed: boolean;
  error?: string;
};

export type ResourceSample = {
  created_at: number;
  system_cpu: number | null;
  system_memory: number | null;
  system_disk: number | null;
  system_available_mb: number | null;
  system_swap: number | null;
  containers?: Record<string, ContainerMetric>;
};

export type Incident = {
  id: number;
  incident_key: string;
  kind: string;
  severity: 'info' | 'warning' | 'critical' | string;
  title: string;
  body: string;
  resolution_body: string;
  status: 'open' | 'resolved';
  category: 'channel' | 'latency' | 'resource' | 'container' | 'service' | 'collector' | 'other' | string;
  duration_seconds: number;
  legacy_cause_missing: boolean;
  started_at: number;
  updated_at: number;
  resolved_at: number | null;
  last_notified_at: number;
  metadata?: {
    provider?: string;
    official_id?: string;
    source_url?: string;
    impact?: string;
    phase?: string;
    component_id?: string;
    component_name?: string;
    timeline?: ProviderStatusUpdate[];
  };
};

export type IncidentSummary = {
  open: number;
  critical_open: number;
  warning_open: number;
  resolved: number;
  resolved_24h: number;
  average_resolution_seconds: number;
};

export type IncidentPayload = {
  generated_at: number;
  total: number;
  limit: number;
  offset: number;
  summary: IncidentSummary;
  items: Incident[];
};

export type CollectorHealth = {
  status: 'ok' | 'starting' | 'stale' | string;
  age_seconds: number;
  stale_after_seconds: number;
  last_attempt_at?: number;
  last_success_at?: number;
  consecutive_failures: number;
  last_error?: string;
};

export type SystemHealth = {
  status: 'ok' | 'degraded';
  database: string;
  database_error?: string;
  monitor_worker: string;
  monitor_error?: string;
  collectors: Record<string, CollectorHealth>;
  timestamp: number;
};
