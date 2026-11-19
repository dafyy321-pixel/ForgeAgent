import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { apiURL } from './api-url';

test('preview opens the requested artifact and downloading keeps its original bytes', async ({
  page,
  request,
}) => {
  const created = await request.post(`${apiURL}/v1/examples/smoke`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  });
  expect(created.status()).toBe(202);
  const run = await created.json();
  const rows = ['summary', 'remote'].map((kind) => ({
    id: kind,
    run_id: run.id,
    task_title: run.title,
    project: run.project,
    created_at: run.started,
    runId: run.id,
    name: kind + '.file',
    type: kind,
    kind,
    digest: 'digest-' + kind,
    size: '3 bytes',
    content: '',
    version: 1,
    verified: false,
  }));
  await page.route('**/v1/catalog/artifacts?*', (route) =>
    route.fulfill({ json: { items: rows, next_cursor: null } }),
  );
  await page.route('**/v1/workspace?*', async (route) => {
    const data = await (await route.fetch()).json();
    await route.fulfill({ json: { ...data, runs: [run], artifacts: rows } });
  });
  const bytes = Buffer.from([0, 128, 255]);
  await page.route('**/v1/artifacts/remote/download', (route) =>
    route.fulfill({ contentType: 'application/octet-stream', body: bytes }),
  );
  await page.goto('/artifacts');
  await page.getByRole('button', { name: 'remote.file', exact: true }).click();
  await expect(page).toHaveURL(/artifact=remote/);
  await page.reload();
  await expect(page.locator('.artifact-heading h3')).toHaveText('remote.file');
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载文件', exact: true }).click();
  const download = await pending;
  expect(await readFile((await download.path())!)).toEqual(bytes);
});

test('a failed workspace snapshot is retried for the same revision', async ({ page }) => {
  let version = 'initial';
  let failed = false;
  await page.route('**/v1/workspace/revision', (route) =>
    route.fulfill({ json: { revision: version } }),
  );
  await page.route('**/v1/workspace', async (route) => {
    if (version === 'changed' && !failed) {
      failed = true;
      return route.fulfill({ status: 503, json: { message: 'retry-snapshot' } });
    }
    return route.continue();
  });
  await page.goto('/runs');
  await expect(page.locator('.mode-label')).toHaveText('已连接后端');
  version = 'changed';
  await expect(page.getByRole('alert').filter({ hasText: 'retry-snapshot' })).toBeVisible();
  await expect(page.locator('.mode-label')).toHaveText('已连接后端');
  const filter = page.getByRole('button', { name: '需要处理', exact: true });
  await filter.focus();
  await page.keyboard.press('Space');
  await expect(filter).toHaveAttribute('aria-pressed', 'true');
});

test('an open approvals catalog refreshes on revision change and retains its loaded depth', async ({
  page,
}) => {
  let version = 1;
  await page.route('**/v1/workspace/revision', (route) =>
    route.fulfill({ json: { revision: String(version) } }),
  );
  await page.route('**/v1/catalog/approvals?*', (route) => {
    const older = new URL(route.request().url()).searchParams.has('cursor');
    return route.fulfill({
      json: {
        items: [
          {
            id: older ? 'old' : 'new',
            run_id: 'run',
            tool: older ? 'older-item' : 'current-' + version,
            status: 'pending',
            reason: '',
          },
        ],
        next_cursor: older ? null : 'next-' + version,
      },
    });
  });
  await page.goto('/approvals');
  await page.getByRole('button', { name: '加载更多审批' }).click();
  await expect(page.getByRole('heading', { name: 'older-item' })).toBeVisible();
  version++;
  await expect(page.getByRole('heading', { name: 'current-2' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'older-item' })).toBeVisible();
});

test('the open inbox shows newly waiting work and removes a resolved approval', async ({
  page,
  request,
}) => {
  const response = await request.post(`${apiURL}/v1/examples/smoke`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  });
  expect(response.status()).toBe(202);
  const waiting = {
    ...(await response.json()),
    title: 'pending approval update',
    status: 'WAITING',
    waitReason: 'APPROVAL',
  };
  let revision = 1;
  await page.route('**/v1/workspace/revision', (route) =>
    route.fulfill({ json: { revision: String(revision) } }),
  );
  await page.route('**/v1/runs?*', (route) =>
    route.fulfill({
      json: { items: revision === 2 ? [waiting] : [], next_cursor: null },
    }),
  );
  await page.goto('/inbox');
  const main = page.locator('#main-content');
  await expect(main.getByText('当前没有待处理事项')).toBeVisible();
  revision = 2;
  await expect(main.getByText(waiting.title, { exact: true })).toBeVisible();
  revision = 3;
  await expect(main.getByText('当前没有待处理事项')).toBeVisible();
});
