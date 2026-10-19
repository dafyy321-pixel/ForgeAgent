import { test, expect } from '@playwright/test';
import { apiURL } from './api-url';

test('revision refresh preserves loaded task and event depth using fresh permissions', async ({
  page,
  request,
}) => {
  const response = await request.post(`${apiURL}/v1/examples/smoke`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  });
  expect(response.status()).toBe(202);
  const run = await response.json();
  const id = run.id;
  let revision = 1;
  const event = (n: number) => ({
    id: `e${n}`,
    runId: id,
    time: Date.now(),
    kind: 'task',
    title: `审查事件 ${n}`,
    detail: 'progress',
  });
  await page.route('**/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/v1/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/v1/workspace/revision')
      return route.fulfill({ json: { revision: String(revision) } });
    if (path === '/v1/workspace') {
      const older = !!url.searchParams.get('cursor');
      const runs = (older ? [2, 3] : revision === 1 ? [0, 1] : [0]).map((n) => ({
        ...run,
        id: `r${n}`,
        title: `分页任务 ${n}`,
      }));
      if (url.searchParams.has('run_id')) runs.push(run);
      return route.fulfill({
        json: {
          schema: 2,
          runs,
          approvals: [],
          artifacts: [],
          skills: [],
          memories: [],
          events: [event(3), event(2)],
          event_next_cursor: 'older-events',
          next_cursor: older ? null : 'older-runs',
          settings: { budget: 5, concurrency: 2, notifications: true, redact: true },
          settings_revision: 1,
          projects: [],
          evalCompleted: false,
        },
      });
    }
    if (path === '/v1/catalog/events')
      return route.fulfill({ json: { items: [event(1), event(0)], next_cursor: null } });
    if (path.endsWith('/events/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: '' });
    return route.fulfill({
      json: path.includes('/catalog/') ? { items: [], next_cursor: null } : {},
    });
  });
  await page.goto('/runs');
  await expect(page.locator('.task-row')).toHaveCount(2);
  await page.getByRole('button', { name: '加载更多任务' }).click();
  await expect(page.locator('.task-row')).toHaveCount(4);
  revision++;
  await expect(page.locator('.task-row')).toHaveCount(3);
  await expect(
    page.locator('#main-content').getByText('分页任务 3', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('分页任务 1', { exact: true })).toHaveCount(0);
  await page.goto(`/tasks/${id}?view=timeline`);
  await page.getByRole('button', { name: '加载更早记录' }).click();
  await expect(page.getByText('审查事件 0', { exact: true })).toBeVisible();
  revision++;
  await expect.poll(async () => page.getByRole('button', { name: '加载更早记录' }).count()).toBe(0);
  await expect(page.getByText('审查事件 0', { exact: true })).toBeVisible();
});

test('draft namespaces separate identities, tenants and legacy drafts', async ({ page }) => {
  await page.goto('/');
  const result = await page.evaluate(async () => {
    const { draftKey, setDraftPrincipal, clearAccountDrafts } = await import('/src/drafts.ts');
    localStorage.setItem('forge-chat-draft-v1', 'legacy private draft');
    setDraftPrincipal('https://issuer', 'alice', 'tenant-a');
    const alice = draftKey('chat');
    localStorage.setItem(alice, 'alice private draft');
    setDraftPrincipal('https://issuer', 'bob', 'tenant-a');
    const bob = localStorage.getItem(draftKey('chat'));
    setDraftPrincipal('https://issuer', 'alice', 'tenant-b');
    const otherTenant = localStorage.getItem(draftKey('chat'));
    setDraftPrincipal('https://issuer', 'alice', 'tenant-a');
    const restored = localStorage.getItem(draftKey('chat'));
    clearAccountDrafts();
    return { bob, otherTenant, restored, removed: localStorage.getItem(alice) };
  });
  expect(result).toEqual({
    bob: null,
    otherTenant: null,
    restored: 'alice private draft',
    removed: null,
  });
});

test('inbox distinguishes loading, error and empty; skills give a next step', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = true;
  await page.route('**/v1/runs?*', async (route) => {
    await gate;
    await route.fulfill(
      fail
        ? { status: 503, json: { code: 'UNAVAILABLE', message: 'test unavailable' } }
        : { json: { items: [], next_cursor: null } },
    );
  });
  await page.goto('/inbox');
  const main = page.locator('#main-content');
  await expect(main.getByRole('status')).toContainText('正在加载');
  await expect(main.getByText('当前没有待处理事项')).toHaveCount(0);
  release();
  await expect(main.getByRole('alert')).toContainText('加载失败');
  fail = false;
  await main.getByRole('button', { name: '重试', exact: true }).click();
  await expect(main.getByText('当前没有待处理事项')).toBeVisible();
  await page.route('**/v1/catalog/skills?*', (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.goto('/skills');
  await expect(main.getByRole('heading', { name: '暂无技能' })).toBeVisible();
  await expect(
    main.locator('.empty-state').getByRole('button', { name: '登记技能版本' }),
  ).toBeVisible();
});

test('mobile primary actions keep the Forge icons and have 44px touch targets', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/');
  for (const control of [
    page.getByRole('combobox', { name: '选择工作项目' }),
    page.getByRole('button', { name: '接入项目', exact: true }),
    page.getByRole('button', { name: '发送消息' }),
    page.getByRole('button', { name: /搜索任务与页面/ }),
  ]) {
    const size = await control.boundingBox();
    expect(size?.height).toBeGreaterThanOrEqual(44);
    expect(size?.width).toBeGreaterThanOrEqual(44);
  }
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).toBeTruthy();
});
