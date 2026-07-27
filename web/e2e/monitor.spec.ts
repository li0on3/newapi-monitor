import { expect, test, type Page, type Route } from '@playwright/test';

type Role = 'viewer' | 'operator' | 'admin';

const now = 1_784_918_400;

const summary = {
  generated_at: now,
  channels: { total: 1, healthy: 1, failed: 0, unknown: 0, last_checked_at: now },
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
  await expect(page.getByRole('button', { name: '创建至今' })).toBeVisible();

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
