import { expect, test, type Page, type Route } from '@playwright/test';

type Role = 'viewer' | 'operator' | 'admin';

const now = 1_784_918_400;

const summary = {
  generated_at: now,
  channels: { total: 1, healthy: 1, delayed: 0, failed: 0, unknown: 0, last_checked_at: now },
  requests: {
    window_seconds: 86_400,
    total: 20,
    slow: 0,
    slow_ratio: 0,
    average_seconds: 2.5,
    p95_seconds: 4.2,
    average_frt_ms: 500,
    last_request_at: now,
  },
  resources: {
    created_at: now,
    system_cpu: 12,
    system_memory: 38,
    system_disk: 42,
    system_available_mb: 4096,
    system_swap: 0,
    containers: {},
  },
  incidents: { open: 0, critical: 0 },
};

const channels = {
  items: [{
    channel_id: 1,
    name: '演示渠道',
    channel_type: 1,
    enabled: true,
    raw_status: 1,
    models: ['gpt-demo'],
    group: 'default',
    synced_at: now,
    latest: {
      observed_at: now,
      success: true,
      elapsed_ms: 1200,
      frt_ms: 320,
      message: '验证通过',
      source: 'real',
    },
    history: [],
    availability: { window_seconds: 604_800, total: 10, successes: 10, percentage: 100 },
    usage_24h: { requests: 20, slow: 0, average_seconds: 2.5, p95_seconds: 4.2, last_request_at: now },
  }],
};

const resources = {
  samples: [
    { created_at: now - 86_400, system_cpu: 8, system_memory: 35, system_disk: 42, system_available_mb: 4300, system_swap: 0, containers: {} },
    { created_at: now, system_cpu: 12, system_memory: 38, system_disk: 42, system_available_mb: 4096, system_swap: 0, containers: {} },
  ],
  requested_start: now - 86_400,
  actual_start: now - 86_400,
  actual_end: now,
  coverage_ratio: 1,
  bucket_seconds: 60,
};

const systemStatus = {
  status: 'ok',
  database: 'ok',
  monitor_worker: 'running',
  collectors: {},
  storage: {
    database_bytes: 8 * 1024 * 1024,
    wal_bytes: 1024 * 1024,
    total_bytes: 9 * 1024 * 1024,
    max_bytes: 2 * 1024 * 1024 * 1024,
    over_capacity: false,
    outbox_pending: 0,
    outbox_dead: 0,
    oldest_pending_age_seconds: 0,
  },
  timestamp: now,
};

const cpaQuota = {
  generated_at: now,
  enabled: true,
  configured: true,
  source: 'cpa',
  account_count: 1,
  window_count: 1,
  interval_seconds: 1800,
  repeat_seconds: 21600,
  activity_enabled: true,
  collector: { status: 'ok', age_seconds: 15, stale_after_seconds: 5400, last_success_at: now },
  last_collection: { observed_at: now, credential_count: 1, success_count: 1, failure_count: 0, window_count: 1 },
  thresholds: { alert_remaining_percent: 20, recovery_remaining_percent: 25 },
  summary: { lowest_remaining_percent: 66, at_risk_windows: 0, healthy_windows: 1, error_accounts: 0, earliest_threshold_at: 0 },
  accounts: [{
    credential_id: 'demo-credential',
    provider: 'codex',
    account_label: 'de***@example.com',
    status: 'ok',
    last_attempt_at: now,
    last_success_at: now,
    consecutive_failures: 0,
    last_error: '',
    windows: [{
      credential_id: 'demo-credential', provider: 'codex', account_label: 'de***@example.com', plan_label: 'Pro',
      window_id: 'base:secondary', window_name: 'Main weekly quota', remaining_percent: 66, used_percent: 34,
      reset_at: now + 3 * 86_400, window_seconds: 604_800, observed_at: now, tone: 'healthy',
      activity: { requests_1h: 18, requests_6h: 72, requests_24h: 220, tokens_1h: 12000, tokens_6h: 50000, tokens_24h: 160000, pressure_ratio: 1.25 },
      history: [
        { observed_at: now - 7200, remaining_percent: 70, used_percent: 30, reset_at: now + 3 * 86_400 },
        { observed_at: now, remaining_percent: 66, used_percent: 34, reset_at: now + 3 * 86_400 },
      ],
      forecast: {
        status: 'projected', confidence: 'medium', burn_percent_per_hour: 2, observed_burn_percent_per_hour: 2,
        seconds_to_threshold: 82800, seconds_to_exhaustion: 118800, threshold_at: now + 82800,
        exhaustion_at: now + 118800, reset_before_threshold: true, reset_before_exhaustion: true,
        sample_count: 2, sample_span_seconds: 7200, pressure_ratio: 1.25,
      },
    }],
  }],
};

const analyticsSeries = Array.from({ length: 13 }, (_, index) => ({
  created_at: now,
  username: 'root',
  model_name: `model-${index + 1}`,
  count: index + 1,
  quota: 100 - index,
  token_used: (index + 1) * 10,
}));

const analyticsFlow = [
  { username: 'root', node_name: 'node-a', token_id: 1, token_name: 'main', use_group: 'default', channel_id: 1, channel_name: 'one', model_name: 'model-1', count: 4, quota: 400, token_used: 40 },
  { username: 'root', node_name: 'node-b', token_id: 1, token_name: 'main', use_group: 'default', channel_id: 2, channel_name: 'two', model_name: 'model-1', count: 3, quota: 300, token_used: 30 },
  ...Array.from({ length: 13 }, (_, index) => ({
    username: 'root', node_name: 'node-a', token_id: index + 2, token_name: `key-${index + 2}`,
    use_group: 'default', channel_id: 1, channel_name: 'one', model_name: `model-${index + 1}`,
    count: index + 1, quota: 49 - index, token_used: index + 1,
  })),
];

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  });
}

async function mockMonitorApi(page: Page, role: Role, authenticated = true) {
  let signedIn = authenticated;
  await page.route('**/monitor/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/monitor/api/', '');

    if (path === 'setup/status') return json(route, { required: false, available: true, expires_at: 0 });
    if (path === 'auth/logout' && request.method() === 'POST') {
      signedIn = false;
      return json(route, { authenticated: false });
    }
    if (path === 'auth/me') {
      if (!signedIn) return json(route, { detail: 'Not authenticated' }, 401);
      return json(route, {
        authenticated: true,
        username: role === 'admin' ? 'root' : 'viewer-demo',
        display_name: role === 'admin' ? 'Root' : '演示用户',
        role,
        source: 'newapi',
        dashboard_refresh_seconds: 30,
        key_usage_available: role !== 'viewer',
        console_available: true,
        console_pages: { overview: true, analytics: true, keys: true, logs: true },
        console_global_scope: role === 'admin',
      });
    }
    if (path === 'dashboard/summary') return json(route, summary);
    if (path === 'channels') return json(route, channels);
    if (path === 'resources') return json(route, resources);
    if (path === 'system/status') return json(route, systemStatus);
    if (path === 'settings') return json(route, { values: {} });
    if (path === 'config-audit') return json(route, { items: [] });
    if (path === 'access/users') return json(route, { items: [] });
    if (path === 'channel-settings') return json(route, channels);
    if (path === 'provider-status/openai') return json(route, { available: false });
    if (path === 'cpa-quota/summary') return json(route, cpaQuota);
    if (path === 'console/analytics') return json(route, {
      start_timestamp: now - 86_400,
      end_timestamp: now,
      scope: url.searchParams.get('scope') === 'self' ? 'self' : 'global',
      series: analyticsSeries,
      flow: analyticsFlow,
      stat: { quota: 1300, rpm: 2, tpm: 300 },
      summary: {
        requests: 100,
        attributed_requests: analyticsSeries.reduce((total, item) => total + item.count, 0),
        unattributed_requests: 9,
        model_request_delta: 9,
        flow_requests: analyticsFlow.reduce((total, item) => total + item.count, 0),
        flow_unattributed_requests: 2,
        flow_request_delta: 2,
        quota: 1300,
        attributed_quota: 1222,
        unattributed_quota: 78,
        model_quota_delta: 78,
        flow_quota: 1259,
        flow_quota_delta: 41,
        tokens: analyticsSeries.reduce((total, item) => total + item.token_used, 0),
        models: analyticsSeries.length,
      },
      reconciliation: {
        requests_exact: true,
        quota_exact: true,
        request_source: 'live_logs',
        quota_source: 'live_logs',
        attribution_source: 'hourly_projection',
      },
      quota_per_unit: 500000,
    });

    return json(route, { detail: `Unhandled E2E API route: ${request.method()} ${path}` }, 404);
  });
}

test('管理员可访问运维模块且路由具有独立 URL', async ({ page }) => {
  await mockMonitorApi(page, 'admin');
  await page.goto('/monitor/');

  await expect(page.getByRole('heading', { name: '服务运行态势' })).toBeVisible();
  await expect(page).toHaveURL(/\/monitor\/$/);

  await page.getByRole('button', { name: '机器资源' }).click();
  await expect(page).toHaveURL(/\/monitor\/resources$/);
  await expect(page.getByRole('heading', { name: '机器资源' })).toBeVisible();
  await expect(page.getByRole('button', { name: '今天' })).toHaveClass(/active/);
  await expect(page.getByRole('button', { name: '累计/历史总量' })).toBeVisible();

  await page.getByRole('button', { name: '系统配置' }).click();
  await expect(page).toHaveURL(/\/monitor\/system$/);
  await expect(page.getByText('存储与告警队列')).toBeVisible();
  await expect(page.getByText('9.0 MB')).toBeVisible();
});

test('普通用户只能看到授权的服务页面并阻止直达管理路由', async ({ page }) => {
  await mockMonitorApi(page, 'viewer');
  await page.goto('/monitor/system');

  await expect(page).toHaveURL(/\/monitor\/$/);
  await expect(page.getByRole('heading', { name: '服务运行态势' })).toBeVisible();
  await expect(page.getByRole('button', { name: '概览' })).toBeVisible();
  await expect(page.getByRole('button', { name: '数据看板' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'API 密钥' })).toBeVisible();
  await expect(page.getByRole('button', { name: '使用日志' })).toBeVisible();
  await expect(page.getByRole('button', { name: '机器资源' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '系统配置' })).toHaveCount(0);
  await expect(page.getByText('NEW API')).toHaveCount(0);
});

test('CPA 周配额使用独立管理员页面且普通用户不能直达', async ({ page }) => {
  await mockMonitorApi(page, 'admin');
  await page.goto('/monitor/cpa-quota');

  await expect(page).toHaveURL(/\/monitor\/cpa-quota$/);
  await expect(page.getByRole('heading', { name: 'CPA 周配额' })).toBeVisible();
  await expect(page.getByText('de***@example.com')).toBeVisible();
  await expect(page.getByText('66%').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: '服务运行态势' })).toHaveCount(0);

  const viewer = await page.context().browser()!.newPage();
  await mockMonitorApi(viewer, 'viewer');
  await viewer.goto('/monitor/cpa-quota');
  await expect(viewer).toHaveURL(/\/monitor\/$/);
  await expect(viewer.getByRole('button', { name: '周配额' })).toHaveCount(0);
  await viewer.close();
});

test('退出后刷新仍保持未登录状态', async ({ page }) => {
  await mockMonitorApi(page, 'admin');
  await page.goto('/monitor/');
  await expect(page.getByRole('heading', { name: '服务运行态势' })).toBeVisible();

  await page.getByTitle('退出登录').click();
  await expect(page.getByRole('heading', { name: 'API 服务中心' })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('heading', { name: 'API 服务中心' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '服务运行态势' })).toHaveCount(0);
});

test('数据看板合并隐藏流向并让可见额度总量闭合', async ({ page }) => {
  await mockMonitorApi(page, 'admin');
  await page.goto('/monitor/console/analytics');

  await expect(page.getByRole('heading', { name: '数据看板' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '全局' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', { name: '当前账号' })).toBeVisible();
  await expect(page.getByText('实时日志总量 $0.0026')).toHaveCount(2);
  await expect(page.getByText('其他 1 个模型')).toBeVisible();
  await expect(page.getByText('其他 2 个流向')).toBeVisible();
  await expect(page.getByText('总量与归集明细差额')).toHaveCount(2);
  await expect(page.getByText('9 条请求已计入实时总数，但未进入当前模型与 Token 归集明细')).toBeVisible();
  await expect(page.getByText('2 条请求已计入实时总数，但未进入当前密钥与分组流向明细')).toBeVisible();
  await expect(page.getByText('$0.0014')).toBeVisible();
});
