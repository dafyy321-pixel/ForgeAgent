import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { apiURL } from './api-url';

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'wait' });
});

test('keyboard search focuses the query, restores focus and opens the result project', async ({
  page,
  request,
}) => {
  const created = await request.post(apiURL + '/v1/examples/smoke', {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  });
  const run = await created.json();
  await page.goto('/runs?project=old-project&q=before');
  const listSearch = page.getByRole('textbox', { name: '搜索任务', exact: true });
  await listSearch.focus();
  await page.keyboard.press('Control+k');
  const input = page.getByRole('textbox', { name: '全局搜索' });
  await expect(input).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(listSearch).toBeFocused();
  await page.keyboard.press('Control+k');
  await page.getByLabel('搜索所有项目').check();
  await page.keyboard.press('Tab');
  await input.fill(run.id);
  await page.locator('.command-result').filter({ hasText: run.id }).click();
  await expect(page).toHaveURL(new RegExp('/tasks/' + run.id + '.*project=runtime-lab'));
  await page.getByRole('button', { name: '返回任务列表' }).click();
  await expect(page).toHaveURL(/\/runs\?project=old-project&q=before/);
  await expect(page.locator('main .empty-state')).toContainText('before');
});

test('mobile inspector navigation dismisses the dialog and moves focus to the new page', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '显示辅助信息' }).click();
  await page.getByRole('dialog').getByRole('button', { name: '查看运行分析' }).click();
  await expect(page).toHaveURL(/observability/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest('main')))
    .toBe(true);
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).not.toBe('hidden');
});

test('approval history pins the selected record through reload and unknown IDs never fall back', async ({
  page,
  request,
}) => {
  const run = await (
    await request.post(apiURL + '/v1/examples/smoke', {
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
  ).json();
  const item = {
    runId: run.id,
    run_id: run.id,
    tool: 'record',
    title: '操作',
    target: 'project',
    impact: '单项',
    risk: 'write',
    version: run.version,
    expiresAt: Date.now() + 3600000,
    diff: '{}',
    reason: '',
  };
  const decisions = [
    { ...item, id: 'current', status: 'pending', title: '待处理操作' },
    { ...item, id: 'old', status: 'denied', title: '过去的操作' },
  ];
  await page.route('**/v1/catalog/approvals?*', (r) =>
    r.fulfill({ json: { items: decisions, next_cursor: null } }),
  );
  await page.route('**/v1/workspace?*', async (r) => {
    const data = await (await r.fetch()).json();
    await r.fulfill({ json: { ...data, runs: [run], approvals: decisions } });
  });
  await page.goto('/approvals');
  await page
    .locator('.task-row')
    .filter({ hasText: '已拒绝' })
    .getByRole('button', { name: '查看记录' })
    .click();
  await expect(page).toHaveURL(/approval=old/);
  await page.reload();
  await expect(page.locator('.review-summary')).toContainText('过去的操作');
  await expect(page.getByRole('button', { name: '批准当前操作' })).toHaveCount(0);
  await page.goto('/tasks/' + run.id + '?view=review&approval=missing');
  await expect(page.getByRole('button', { name: '批准当前操作' })).toHaveCount(0);
  await expect(page.getByText('这条审批记录不可用')).toBeVisible();
});

test('task history export includes older event pages', async ({ page, request }) => {
  const run = await (
    await request.post(apiURL + '/v1/examples/smoke', {
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
  ).json();
  const event = (id: string) => ({
    id,
    runId: run.id,
    time: 100,
    kind: 'task',
    title: id,
    detail: '',
  });
  await page.route('**/v1/workspace?*', async (r) => {
    const data = await (await r.fetch()).json();
    await r.fulfill({ json: { ...data, events: [event('new')], event_next_cursor: 'older' } });
  });
  await page.route('**/v1/catalog/events?*', async (r) => {
    const older = new URL(r.request().url()).searchParams.has('cursor');
    await r.fulfill({
      json: { items: [event(older ? 'old' : 'new')], next_cursor: older ? null : 'older' },
    });
  });
  await page.goto('/tasks/' + run.id);
  await page.getByLabel('更多任务操作').click();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出任务记录' }).click();
  const file = await pending;
  expect(
    JSON.parse(await readFile((await file.path())!, 'utf8')).events.map(
      (e: { id: string }) => e.id,
    ),
  ).toEqual(['new', 'old']);
});
