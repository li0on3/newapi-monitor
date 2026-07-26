import { expect, test, type Page } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  const response = await request.post('http://127.0.0.1:18083/monitor/api/e2e/reset');
  expect(response.ok()).toBe(true);
});

async function login(page: Page) {
  await page.goto('deliveries');
  await page.locator('input[autocomplete="current-password"]').fill('E2E-Admin-Password-2026!');
  await page.getByRole('button', { name: '进入监控台' }).click();
  await expect(page.getByRole('heading', { name: '告警投递中心' })).toBeVisible();
}

test('真实后端支持死信恢复、批量取消与投递筛选', async ({ page }) => {
  await login(page);

  await page.getByRole('button', { name: /死信/ }).first().click();
  await expect(page.getByRole('button', { name: /Webhook 持续失败.*死信/ })).toBeVisible();
  await page.getByRole('button', { name: /Webhook 持续失败.*死信/ }).click();
  await expect(page.locator('.delivery-error-box pre')).toHaveText('synthetic webhook timeout');
  await page.getByRole('button', { name: '恢复死信并重投' }).click();
  await expect(page.getByRole('button', { name: /Webhook 持续失败.*死信/ })).not.toBeVisible();

  await page.getByRole('button', { name: /待投递/ }).first().click();
  await expect(page.getByRole('button', { name: /等待首次投递.*待投递/ })).toBeVisible();
  await page.locator('.delivery-row').filter({ hasText: '等待首次投递' }).getByLabel('选择投递记录').check();
  await page.getByRole('button', { name: '批量取消' }).click();
  await expect(page.getByRole('button', { name: /等待首次投递.*待投递/ })).not.toBeVisible();

  await page.getByRole('button', { name: /已取消/ }).first().click();
  await expect(page.getByRole('button', { name: /等待首次投递.*已取消/ })).toBeVisible();
});

test('真实后端支持事件确认、静默时段与渠道维护窗口', async ({ page }) => {
  await login(page);

  await page.getByRole('button', { name: '事件' }).click();
  await expect(page.getByText('Synthetic upstream timeout').first()).toBeVisible();
  await page.getByPlaceholder('可选：记录负责人、排查动作或工单编号').fill('E2E owner acknowledged');
  await page.getByRole('button', { name: '确认并记录' }).click();
  await expect(page.getByText('事件已确认')).toBeVisible();
  await expect(page.getByText('E2E owner acknowledged')).toBeVisible();

  await page.goto('system/notifications');
  await expect(page.getByRole('heading', { name: '告警通知中心' })).toBeVisible();
  await expect(page.getByText('通知路由已生效')).toBeVisible();
  await page.getByRole('switch', { name: '启用静默时段' }).click();
  await page.locator('.notification-policy-fields input[type="time"]').nth(0).fill('23:00');
  await page.locator('.notification-policy-fields input[type="time"]').nth(1).fill('07:00');
  await page.getByRole('button', { name: '保存通知配置' }).click();
  await expect(page.getByText('通知路由已生效')).toBeVisible();

  await page.goto('channels');
  await expect(page.getByRole('heading', { name: '渠道配置' })).toBeVisible();
  await expect(page.getByText('Synthetic OpenAI').first()).toBeVisible();
  await page.getByRole('switch', { name: '启用计划维护' }).click();
  const start = new Date(Date.now() + 3_600_000).toISOString().slice(0, 16);
  const end = new Date(Date.now() + 7_200_000).toISOString().slice(0, 16);
  await page.locator('.maintenance-window-fields input[type="datetime-local"]').nth(0).fill(start);
  await page.locator('.maintenance-window-fields input[type="datetime-local"]').nth(1).fill(end);
  await page.getByPlaceholder('例如：上游升级或线路切换').fill('E2E maintenance');
  const saveResponse = page.waitForResponse((response) => (
    response.request().method() === 'PUT'
    && response.url().endsWith('/monitor/api/channel-settings/1')
  ));
  await page.getByRole('button', { name: '保存渠道配置' }).click();
  expect((await saveResponse).ok()).toBe(true);
  await expect(page.getByPlaceholder('例如：上游升级或线路切换')).toHaveValue('E2E maintenance');

  const settings = await page.evaluate(async () => (await fetch('/monitor/api/settings')).json());
  expect(settings.values.notification_quiet_hours_enabled).toBe(true);
  expect(settings.values.notification_quiet_hours_start).toBe('23:00');
  await expect.poll(async () => page.evaluate(async () => {
    const channels = await (await fetch('/monitor/api/channel-settings')).json();
    return channels.items[0].monitor_config;
  })).toMatchObject({
    maintenance_window_enabled: true,
    maintenance_window_reason: 'E2E maintenance',
  });
});
