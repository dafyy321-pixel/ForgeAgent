import { test, expect } from '@playwright/test';
import { apiURL } from './api-url';

test('task creation pins evidence recovery and renders recovery history', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '创建任务', exact: true }).first()).toBeVisible();
  await page.keyboard.press('Alt+n');
  const form = page.getByRole('dialog', { name: '创建任务', exact: true });
  await form.getByLabel('任务目标', { exact: true }).fill('Evidence recovery UI');
  await form.getByLabel('任务说明', { exact: true }).fill('修复加法并验证。');
  await form.getByRole('button', { name: /运行配置/ }).click();
  await form.getByLabel('模型', { exact: true }).selectOption('fixture');
  await form.getByRole('checkbox', { name: /启用证据驱动恢复/ }).check();
  const request = page.waitForRequest((r) => r.url().endsWith('/v1/runs') && r.method() === 'POST');
  await form.getByRole('button', { name: '创建任务', exact: true }).click();
  expect((await request).postDataJSON().harness.evidence_recovery).toBe(true);
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/);
  await expect(page.locator('#main-content .run-badge').first()).toHaveText('已验证完成', {
    timeout: 30000,
  });
  await page.getByText('执行详情', { exact: true }).click();
  await page
    .getByRole('navigation', { name: '任务工作区' })
    .getByRole('button', { name: '恢复诊断', exact: true })
    .click();
  await expect(page.getByText('证据与恢复记录', { exact: true })).toBeVisible();
  await expect(page.getByText(/暂无记录。创建任务时可/)).toBeVisible();
});

test('failed independent acceptance displays persisted uncertain diagnosis and evidence', async ({
  page,
  request,
}) => {
  const created = await request.post(`${apiURL}/v1/runs`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    data: {
      project_id: 'runtime-lab',
      model: 'configured',
      task: { goal: 'Inspect failed acceptance', allowed_paths: ['src'] },
      harness: { evidence_recovery: true },
    },
  });
  expect(created.status()).toBe(202);
  const { id } = await created.json();
  await expect
    .poll(async () => (await (await request.get(`${apiURL}/v1/runs/${id}`)).json()).status, {
      timeout: 20000,
    })
    .toBe('PAUSED');
  await expect
    .poll(
      async () => {
        const run = await (await request.get(`${apiURL}/v1/runs/${id}`)).json();
        return (
          await request.post(`${apiURL}/v1/runs/${id}/verify`, {
            data: { expected_version: run.stateVersion },
          })
        ).status();
      },
      { timeout: 10000 },
    )
    .toBe(202);
  await expect
    .poll(
      async () =>
        (await (await request.get(`${apiURL}/v1/runs/${id}/recovery`)).json()).items.length,
      { timeout: 20000 },
    )
    .toBeGreaterThan(0);
  await page.goto(`/tasks/${id}`);
  await page.getByText('执行详情', { exact: true }).click();
  await page
    .getByRole('navigation', { name: '任务工作区' })
    .getByRole('button', { name: '恢复诊断', exact: true })
    .click();
  await expect(page.locator('summary').filter({ hasText: 'Acceptance failed;' })).toContainText(
    'hypothesis',
  );
  await page.getByRole('button', { name: '证据', exact: true }).click();
  await expect(page.locator('summary').filter({ hasText: 'verification' })).toBeVisible();
});
