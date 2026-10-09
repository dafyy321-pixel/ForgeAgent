import { test, expect } from '@playwright/test';

test('overview uses server totals and deliveries reach history independently of the task window', async ({
  page,
}) => {
  await page.route('**/v1/workspace/summary*', (route) =>
    route.fulfill({
      json: {
        total: 58,
        attention: 7,
        active: 3,
        succeeded: 44,
        cost_usd: 12.345,
        reserved_usd: 1,
        attention_runs: [],
        active_runs: [],
        completed_runs: [],
      },
    }),
  );
  await page.route('**/v1/catalog/artifacts*', (route) => {
    const url = new URL(route.request().url());
    const second = url.searchParams.has('cursor');
    return route.fulfill({
      json: {
        items: [
          {
            id: second ? 'old' : 'recent',
            run_id: 'not-in-workspace',
            name: second ? 'historic.patch' : 'recent.patch',
            kind: 'patch',
            verified: true,
            version: 1,
          },
        ],
        next_cursor: second ? null : 'next-page',
      },
    });
  });
  await page.goto('/');
  await expect(page.getByText('58 个任务 · 44 个已验证完成', { exact: true })).toBeVisible();
  await expect(page.locator('.overview-stats')).toContainText('07');
  await expect(page.getByText('$12.35', { exact: true })).toBeVisible();
  await page.goto('/artifacts');
  await expect(page.getByRole('button', { name: 'recent.patch', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '加载更多交付物' }).click();
  await expect(page.getByRole('button', { name: 'historic.patch', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'recent.patch', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '加载更多交付物' })).toHaveCount(0);
});
