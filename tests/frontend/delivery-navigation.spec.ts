import { expect, test } from '@playwright/test';
import { apiURL } from './api-url';

test('code changes open the versioned patch, preserve the link and explain a missing patch', async ({
  page,
  request,
}) => {
  const created = await request.post(`${apiURL}/v1/examples/smoke`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  });
  expect(created.status()).toBe(202);
  const { id } = await created.json();
  await page.goto(`/tasks/${id}`);
  const changes = page.getByRole('button', { name: '查看代码变更', exact: true });
  await changes.click({ timeout: 30000 });
  await expect(page).toHaveURL(/[?&]artifact=[^&]+/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'changes.patch' })).toBeVisible();
  await page.route('**/v1/workspace?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    // An old patch exists, but the current version has no patch.
    for (const a of body.artifacts) if (a.runId === id && a.name === 'changes.patch') a.version--;
    await route.fulfill({ response, json: body });
  });
  await page.goto(`/tasks/${id}`);
  await expect(page.getByText('当前版本没有代码补丁')).toBeVisible();
  await expect(changes).toHaveCount(0);
  await expect(page.getByRole('button', { name: '查看测试结果' })).toBeVisible();
  await page.getByRole('button', { name: '继续讨论修改' }).click();
  const reference = page.locator('.followup-reference');
  await expect(reference).toContainText('上轮任务要求');
  await expect(reference).toContainText('上轮交付摘要');
  await expect(reference).toContainText('两个输入之和');
  await expect
    .poll(() =>
      reference.evaluate((el) => {
        const rect = el.getBoundingClientRect();
        return el.contains(
          document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2),
        );
      }),
    )
    .toBe(true);
  await page.goto('/artifacts');
  const delivery = page.getByRole('region', { name: '验证 Runtime 端到端流程' }).first();
  await expect(delivery).toContainText('验证 Runtime 端到端流程');
  await expect(delivery).toContainText(/runtime-lab.*v2/);
  await expect(delivery.locator('time')).toHaveAttribute('datetime', /T/);
  await page.unrouteAll({ behavior: 'wait' });
});
