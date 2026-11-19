import { test, expect } from '@playwright/test';
import { apiURL } from './api-url';

test('homepage explains the workflow, preserves the draft and starts a read-only conversation', async ({
  page,
}) => {
  await page.route('**/v1/workspace/model-status', (route) =>
    route.fulfill({ json: { available: true } }),
  );
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '今天想对项目做些什么？' })).toBeVisible();
  await page.getByRole('button', { name: '增加一个功能', exact: true }).click();
  const composer = page.getByRole('textbox', { name: '描述你的需求，或粘贴遇到的问题…' });
  await expect(composer).toHaveValue(/先检查现有实现/);
  await composer.fill('先分析项目中的搜索功能');
  await composer.press('Shift+Enter');
  await composer.press('a');
  await expect(composer).toHaveValue('先分析项目中的搜索功能\na');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect(page.getByRole('alert')).toContainText('先选择一个项目');
  await page.getByRole('combobox', { name: '本次任务的项目' }).selectOption('runtime-lab');
  await page.reload();
  await expect(composer).toHaveValue('先分析项目中的搜索功能\na');
  await expect(page.getByRole('combobox', { name: '本次任务的项目' })).toHaveValue('runtime-lab');
  await expect(page.getByRole('button', { name: '发送消息' })).toBeEnabled();
  const sent = page.waitForRequest((r) => r.url().endsWith('/v1/runs') && r.method() === 'POST');
  await composer.press('Enter');
  const spec = (await sent).postDataJSON();
  expect(spec.task.mode).toBe('discuss');
  expect(spec.capabilities).toEqual(['repo.read']);
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/);
  await expect(page.getByLabel('任务对话')).toContainText('先分析项目中的搜索功能');
  await page.reload();
  await expect(page.getByLabel('任务对话')).toContainText('先分析项目中的搜索功能');
  await page.setViewportSize({ width: 390, height: 900 });
  await page
    .getByRole('navigation', { name: '手机快捷导航' })
    .getByRole('button', { name: '新对话', exact: true })
    .click();
  await expect(page.getByRole('heading', { name: '今天想对项目做些什么？' })).toBeVisible();
});

async function pausedDiscussion(request: import('@playwright/test').APIRequestContext) {
  const response = await request.post(`${apiURL}/v1/runs`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    data: {
      project_id: 'runtime-lab',
      model: 'configured',
      capabilities: ['repo.read'],
      task: { mode: 'discuss', goal: '讨论并保留小数加法' },
    },
  });
  expect(response.status()).toBe(202);
  const { id } = await response.json();
  await expect
    .poll(async () => (await (await request.get(`${apiURL}/v1/runs/${id}`)).json()).status, {
      timeout: 20000,
    })
    .toBe('PAUSED');
  return id as string;
}

test('delivery keeps manual criteria visible and failed tasks offer a new attempt', async ({
  page,
  request,
}) => {
  const response = await request.post(`${apiURL}/v1/runs`, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    data: {
      project_id: 'runtime-lab',
      model: 'fixture',
      task: {
        goal: 'Fix add',
        criteria: ['核对示例的措辞'],
        acceptance_conditions: [{ kind: 'file_exists', path: 'src/calculator.py' }],
      },
    },
  });
  expect(response.status()).toBe(202);
  const { id } = await response.json();
  await page.goto(`/tasks/${id}`);
  await expect(page.locator('.chat-delivery')).toContainText('1 项人工条件待核对', {
    timeout: 30000,
  });
  await page.getByRole('button', { name: '查看测试结果' }).click();
  await expect(page.getByRole('heading', { name: '人工验收条件' })).toBeVisible();
  await expect(page.locator('#main-content')).toContainText('核对示例的措辞');
  await page.route('**/v1/workspace?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const item = body.runs.find((r: { id: string }) => r.id === id);
    item.status = 'FAILED';
    item.verification = {
      version: 1,
      status: 'inconclusive',
      checks: [{ name: '环境', evidence: '环境不可用', status: 'not_run' }],
    };
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  await expect(
    page.locator('.verification-check').filter({ hasText: 'src/calculator.py' }),
  ).toContainText('未运行');
  await page.locator('.more-menu summary').click();
  await expect(
    page.locator('.more-menu').getByRole('button', { name: '创建新的尝试' }),
  ).toBeVisible();
  await expect(page.locator('.more-menu').getByRole('button', { name: '取消任务' })).toHaveCount(0);
  await page.unrouteAll({ behavior: 'wait' });
});

test('plan confirmation UI waits for explicit confirmation and then opens the execution', async ({
  page,
  request,
}, testInfo) => {
  const id = await pausedDiscussion(request);
  let executionId: string | null = null;
  const current = await (await request.get(`${apiURL}/v1/runs/${id}`)).json();
  // Only the plan-ready presentation is simulated; the independent backend test covers model-to-plan transitions.
  await page.route('**/v1/workspace?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.runs = body.runs.map((run: { id: string }) =>
      run.id === id
        ? { ...run, planReady: !executionId, inputRequired: !executionId, executionId }
        : run,
    );
    await route.fulfill({ response, json: body });
  });
  await page.route(`**/v1/runs/${id}/conversation`, (route) =>
    route.fulfill({
      json: {
        initial: '讨论并保留小数加法',
        messages: [
          {
            id: 'plan',
            role: 'assistant',
            content:
              '## 实现方案\n修复 **加法**，保留小数。修改 `src/calculator.py`，参考 docs/guide.md。\n- 运行独立测试\n```python\nreturn left + right\n```',
          },
        ],
        plan_ready: true,
        version: current.stateVersion,
        plan_digest: 'reviewed-plan',
      },
    }),
  );
  let confirmations = 0;
  await page.route(`**/v1/runs/${id}/execute`, async (route) => {
    confirmations++;
    const body = route.request().postDataJSON();
    expect(body.spec.project_id).toBe('runtime-lab');
    expect(body.spec.task.mode).toBe('execute');
    expect(body.spec.task.allowed_paths).toEqual(['src/calculator.py']);
    expect(body.spec.task.scope).toContain('src/calculator.py');
    expect(body.spec.task.scope).not.toContain('只读讨论');
    expect(body.spec.task.acceptance_conditions).toEqual([
      { kind: 'file_exists', path: 'src/calculator.py' },
    ]);
    expect(body.spec.task.criteria).toEqual(['用户认可计算结果的展示方式']);
    expect(body.expected_version).toBe(current.stateVersion);
    expect(body.plan_digest).toBe('reviewed-plan');
    const execution = await request.post(`${apiURL}/v1/examples/smoke`, {
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
    const result = await execution.json();
    executionId = result.id;
    await route.fulfill({ status: 202, json: result });
  });
  await page.goto(`/tasks/${id}`);
  await expect(page.getByLabel('任务对话')).toContainText('保留小数');
  expect(confirmations).toBe(0);
  await page.screenshot({ path: testInfo.outputPath('conversation-plan.png'), fullPage: true });
  await page.getByRole('button', { name: '确认方案并执行', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确认方案并执行', exact: true });
  await expect(dialog.getByLabel('项目', { exact: true })).toBeDisabled();
  await expect(dialog.getByLabel('允许写入的路径')).toHaveValue('');
  await dialog.getByRole('button', { name: '确认并开始执行', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('填写允许写入的文件或目录');
  await dialog.getByRole('button', { name: '允许写入 src/calculator.py', exact: true }).click();
  await expect(dialog.getByLabel('允许写入的路径')).toHaveValue('src/calculator.py');
  await dialog.getByRole('button', { name: '确认并开始执行', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('核对方案中其余文件');
  await dialog.getByLabel('其余文件仅供读取').check();
  await dialog.getByLabel('必须存在的文件').fill('src/calculator.py');
  await dialog.getByLabel('人工验收条件').fill('用户认可计算结果的展示方式');
  expect(confirmations).toBe(0);
  await dialog.getByRole('button', { name: '确认并开始执行', exact: true }).click();
  await expect(page.locator('#main-content .run-badge').first()).toHaveText('已验证完成', {
    timeout: 30000,
  });
  expect(confirmations).toBe(1);
  await expect(page.getByRole('button', { name: '下载补丁', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '继续讨论修改', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '基于这次结果，你还想修改什么？' })).toBeVisible();
  for (const width of [1440, 720, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    ).toBeTruthy();
    await page.screenshot({
      path: testInfo.outputPath(`conversation-${width}.png`),
      fullPage: true,
    });
  }
  await page.goto(`/tasks/${id}`);
  await expect(page.getByRole('button', { name: '查看执行任务', exact: true })).toBeVisible();
  await expect(page.locator('.state-banner')).toHaveCount(0);
  await page.unrouteAll({ behavior: 'wait' });
});

test('an uncertain message retries the same payload and key after a reload', async ({
  page,
  request,
}) => {
  const id = await pausedDiscussion(request);
  const attempts: { key: string | undefined; body: unknown }[] = [];
  await page.route(`**/v1/runs/${id}/messages`, async (route) => {
    attempts.push({
      key: route.request().headers()['idempotency-key'],
      body: route.request().postDataJSON(),
    });
    if (attempts.length === 1) await route.abort('failed');
    else await route.fulfill({ json: { ok: true } });
  });
  await page.goto(`/tasks/${id}`);
  const input = page.getByRole('textbox', { name: '继续讨论你的需求…' });
  await input.fill('请保留小数行为');
  await input.press('Enter');
  await expect(page.getByRole('alert')).toContainText('NETWORK_ERROR');
  await expect(input).toHaveAttribute('readonly', '');
  await page.reload();
  await expect(input).toHaveValue('请保留小数行为');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect.poll(() => attempts.length).toBe(2);
  expect(attempts[1]).toEqual(attempts[0]);
  await expect(input).toHaveValue('');
});

test('a plan changed in another tab disables its old confirmation', async ({ page, request }) => {
  const id = await pausedDiscussion(request);
  const run = await (await request.get(`${apiURL}/v1/runs/${id}`)).json();
  let version = run.stateVersion;
  await page.route('**/v1/workspace/revision', (route) =>
    route.fulfill({ json: { revision: String(version) } }),
  );
  await page.route('**/v1/workspace?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.runs = body.runs.map((r: { id: string }) =>
      r.id === id ? { ...r, planReady: true, stateVersion: version } : r,
    );
    await route.fulfill({ response, json: body });
  });
  await page.route(`**/v1/runs/${id}/conversation`, (route) =>
    route.fulfill({
      json: {
        initial: '讨论方案',
        version,
        plan_digest: `plan-${version}`,
        plan_ready: true,
        messages: [{ id: 'plan', role: 'assistant', content: `方案版本 ${version}` }],
      },
    }),
  );
  await page.goto(`/tasks/${id}`);
  await page.getByRole('button', { name: '确认方案并执行', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确认方案并执行', exact: true });
  await expect(dialog.getByRole('button', { name: '确认并开始执行' })).toBeEnabled();
  version++;
  await expect(dialog.getByRole('alert')).toContainText('方案已更新');
  await expect(dialog.getByRole('button', { name: '确认并开始执行' })).toBeDisabled();
  await dialog.getByRole('button', { name: '重新阅读方案' }).click();
  await expect(dialog).not.toBeVisible();
});
