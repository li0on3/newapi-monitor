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
  monitor_config?: ChannelMonitorConfig;
};

export type ChannelMonitorConfig = {
  display_enabled?: boolean;
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
  status: 'open' | 'resolved';
  started_at: number;
  updated_at: number;
  resolved_at: number | null;
  last_notified_at: number;
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
