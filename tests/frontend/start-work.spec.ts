import { expect, test, type Page } from '@playwright/test';

const input = (page: Page) =>
  page.getByRole('textbox', { name: '描述你的需求，或粘贴遇到的问题…' });
const project = (page: Page) => page.getByRole('combobox', { name: '本次任务的项目' });

async function catalog(page: Page) {
  await page.route('**/v1/catalog/projects?*', (route) =>
    route.fulfill({ json: { items: [{ id: 'alpha' }, { id: 'beta' }], next_cursor: null } }),
  );
}

test('accounts cannot inherit another account’s target or draft on the same browser tab', async ({
  page,
}) => {
  await page.goto('/?project=alpha');
  await expect(input(page)).toBeVisible();
  const result = await page.evaluate(async () => {
    const {
      setDraftPrincipal,
      rememberChatProject,
      initialChatProject,
      writeChatDraft,
      readChatDraft,
    } = await import('/src/drafts.ts');
    setDraftPrincipal('issuer', 'alice', 'work');
    rememberChatProject('alpha', 'beta');
    writeChatDraft('beta', { text: 'Alice 私有需求' });
    setDraftPrincipal('issuer', 'bob', 'work');
    return { selected: initialChatProject('alpha'), text: readChatDraft('beta').text };
  });
  expect(result).toEqual({ selected: 'alpha', text: '' });
});

test('an older draft follows its original project through editing and refresh', async ({
  page,
}) => {
  await catalog(page);
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('legacy-seeded')) {
      localStorage.setItem(
        'forge-draft:local:chat',
        JSON.stringify({ text: '迁移前的需求', project: 'alpha', budget: 5 }),
      );
      sessionStorage.setItem('legacy-seeded', '1');
    }
  });
  await page.goto('/?project=beta');
  await expect(input(page)).toHaveValue('');
  await page.goto('/');
  await expect(input(page)).toHaveValue('迁移前的需求');
  await input(page).fill('编辑后的需求');
  await page.reload();
  await expect(project(page)).toHaveValue('alpha');
  await expect(input(page)).toHaveValue('编辑后的需求');
});

test('project switching isolates drafts, and inserting a template can be undone after refresh', async ({
  page,
}) => {
  await catalog(page);
  await page.goto('/?project=alpha');
  await input(page).fill('Alpha 复现步骤');
  await page.goto('/?project=beta');
  await expect(input(page)).toHaveValue('');
  await input(page).fill('Beta 需要的新功能');
  await page.goto('/?project=alpha');
  await expect(input(page)).toHaveValue('Alpha 复现步骤');
  await page.getByRole('button', { name: '增加一个功能', exact: true }).click();
  await expect(input(page)).toHaveValue(/Alpha 复现步骤[\s\S]*使用场景/);
  await page.reload();
  await page.getByRole('button', { name: '撤销插入模板' }).click();
  await expect(input(page)).toHaveValue('Alpha 复现步骤');
  await project(page).selectOption('beta');
  await expect(input(page)).toHaveValue('Beta 需要的新功能');
});

test('first discussion uses the loaded workspace cap and a manual cap survives reload', async ({
  page,
}) => {
  await catalog(page);
  await page.route('**/v1/workspace/model-status', (route) =>
    route.fulfill({ json: { available: true } }),
  );
  await page.route(/\/v1\/workspace(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ json: { ...body, settings: { ...body.settings, budget: 0.5 } } });
  });
  const budgets: number[] = [];
  await page.route('**/v1/runs', (route) => {
    budgets.push(Number(route.request().postDataJSON().budget.max_cost_usd));
    return route.fulfill({ status: 422, json: { message: '测试保留草稿' } });
  });
  await page.goto('/?project=alpha');
  const cap = page.locator('.chat-budget summary');
  await expect(cap).toHaveText('对话额度 $0.5');
  await input(page).fill('检查额度');
  await input(page).press('Enter');
  await expect.poll(() => budgets).toEqual([0.5]);
  await cap.click();
  await page.getByLabel('本次对话费用上限（USD）').fill('2');
  await page.reload();
  await expect(cap).toHaveText('对话额度 $2');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect.poll(() => budgets).toEqual([0.5, 2]);
});

test('mobile home offers a free example before scrolling and safeguards a model-less draft', async ({
  page,
}) => {
  await catalog(page);
  let available = false;
  await page.route('**/v1/workspace/model-status', (route) =>
    route.fulfill({ json: { available } }),
  );
  await page.route(/\/v1\/workspace(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...(await response.json()), runs: [] } });
  });
  let submissions = 0;
  await page.route('**/v1/runs', (route) => {
    submissions++;
    return route.abort();
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?project=alpha');
  const example = page.getByRole('button', { name: '体验免费示例' });
  await expect(example).toBeInViewport();
  const target = await example.boundingBox();
  expect((target?.y || 0) + (target?.height || 0)).toBeLessThan(778);
  await expect(page.getByText(/Shift \+ Enter/)).toBeHidden();
  await input(page).fill('保留这个需求');
  await expect(page.getByRole('button', { name: '发送消息' })).toBeDisabled();
  await input(page).press('Enter');
  await expect(input(page)).toHaveValue('保留这个需求');
  expect(submissions).toBe(0);
  await expect(page.locator('.recent-work')).toContainText('可以先体验免费示例');
  await page.getByRole('button', { name: '查看模型配置' }).click();
  await expect(page).toHaveURL(/settings/);
  available = true;
  await page.goBack();
  await expect(input(page)).toHaveValue('保留这个需求');
  await expect(page.getByRole('button', { name: '发送消息' })).toBeEnabled();
  await expect(page.getByRole('button', { name: '查看模型配置' })).toHaveCount(0);
});
