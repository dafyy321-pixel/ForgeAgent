import { test, expect } from '@playwright/test';

test('revision refresh preserves loaded task and event depth using fresh permissions', async ({
  page,
  request,
}) => {
  const response = await request.post('http://127.0.0.1:8000/v1/examples/smoke', {
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
