import { test, expect } from '@playwright/test';

test('paired comparison separates repair from expected pause and incomplete cost', async ({
  page,
}) => {
  await page.route('**/v1/evaluations', (route) =>
    route.fulfill({
      json: [
        {
          id: 'controlled',
          dataset: 'controlled@1',
          kind: 'paired',
          status: 'completed',
          model: 'fixture@1',
          independent_cases: 1,
          repetitions: 2,
          successes: 1,
          total: 2,
          summaries: {
            baseline: {
              correct_disposition_rate: 1,
              cluster_95_ci: null,
              cost: 0.05,
              latency_p95: 2,
              repair_rate: 0.5,
              correct_pauses: 1,
              manual_decisions: 2,
              research: {
                full_cost_complete: false,
                missing_cost_components: ['human_seconds'],
                failure_distribution: { provider: 1 },
              },
            },
          },
          comparisons: {
            structure: {
              baseline: 'baseline',
              paired_difference: -1,
              paired_repair_difference: -1,
              negative_transfer_cases: [{ case_id: 'regression-case', repair_difference: -1 }],
              per_project_repair_difference: { 'external-project': -1 },
            },
          },
          results: [
            { id: 'success', config: 'baseline', status: 'SUCCEEDED' },
            { id: 'pause', config: 'baseline', status: 'PAUSED' },
          ],
        },
      ],
    }),
  );
  await page.goto('/evaluations');
  const table = page.getByRole('table', { name: '实验配置比较' });
  await expect(table).toContainText('1 / 2');
  await expect(table).toContainText('100.0%');
  await expect(table).toContainText('PASS 修复率 50.0%');
  await expect(page.getByText('实际修复率差 -100.0%', { exact: false })).toContainText(
    '负迁移任务 1 个',
  );
  await expect(page.getByText('实际修复率差 -100.0%', { exact: false })).toContainText(
    'regression-case',
  );
  await expect(page.getByText('正确暂停 1 次，人工决策 2 次。', { exact: false })).toBeVisible();
  await expect(table).toContainText('样本不足，未计算');
  await expect(table).toContainText('不完整：human_seconds');
  await expect(
    page.getByText('确定性 fixture 机制自检，不代表真实模型修复能力或泛化效果。'),
  ).toBeVisible();
  await expect(page.getByText('provider 1', { exact: false })).toBeVisible();
  await expect(page.getByText('配置与统计原始数据')).toBeVisible();
});

test('case replay shows committed summaries without dispatching new effects', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '运行契约示例', exact: true }).click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/);
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/v1/') && request.method() !== 'GET') writes.push(request.url());
  });
  await page
    .getByRole('navigation', { name: '任务工作区' })
    .getByRole('button', { name: '案例回放', exact: true })
    .click();
  const replay = page.getByRole('region', { name: '案例回放' });
  await expect(replay).toContainText('只读展示已提交的约束');
  await expect(replay).toContainText('RUN_CREATED');
  await expect(replay.getByRole('button', { name: '查看补丁与交付物' })).toBeVisible();
  expect(writes).toEqual([]);
});
