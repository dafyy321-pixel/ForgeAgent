import { test, expect } from '@playwright/test';

for (const entry of ['settings', 'home'] as const) {
  test(`repository wizard validates files and starts a discussion from ${entry}`, async ({
    page,
  }) => {
    let uploaded = false;
    let registered = false;
    let releasePreflight!: () => void;
    const preflightGate = new Promise<void>((resolve) => {
      releasePreflight = resolve;
    });
    await page.route('**/v1/repository-bundles/*', async (route) => {
      expect(route.request().headers()['content-type']).toBe('application/octet-stream');
      expect(route.request().postDataBuffer()?.toString()).toBe('fixture-bundle');
      uploaded = true;
      await route.fulfill({ json: { bundle_digest: 'sha256:fixture', bundle_bytes: 14 } });
    });
    await page.route('**/v1/projects/preflight', async (route) => {
      expect(uploaded).toBe(true);
      const data = route.request().postDataJSON();
      expect(data.repository.commit).toBe('a'.repeat(40));
      expect(data.repository.bundle_base64).toBeUndefined();
      await preflightGate;
      await route.fulfill({
        json: {
          files: 3,
          commit: data.repository.commit,
          baseline_digest: 'sha256:reviewed',
          acceptance_execution: 'not_run',
        },
      });
    });
    await page.route('**/v1/projects', async (route) => {
      registered = true;
      await route.fulfill({ status: 201, json: { id: 'demo' } });
    });
    await page.goto(entry === 'settings' ? '/settings' : '/');
    const composer = page.getByRole('textbox', { name: '描述你的需求，或粘贴遇到的问题…' });
    if (entry === 'home') await composer.fill('接入后，请检查我的项目');
    await page
      .getByRole('button', {
        name: entry === 'settings' ? '接入 Git 仓库' : '接入项目',
        exact: true,
      })
      .click();
    const dialog = page.getByRole('dialog', { name: '接入 Git 仓库', exact: true });
    await expect(dialog.getByText('git rev-parse HEAD', { exact: false }).first()).toBeVisible();
    await expect(dialog.getByRole('button', { name: '登记已预检项目' })).toBeDisabled();
    await dialog.getByLabel('项目 ID', { exact: true }).fill('demo');
    await dialog.getByLabel('完整 Git commit').fill('a'.repeat(40));
    await dialog.getByLabel('Git bundle', { exact: true }).setInputFiles({
      name: 'repo.bundle',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('fixture-bundle'),
    });
    await dialog.getByLabel('验收与依赖 JSON').setInputFiles({
      name: 'acceptance.json',
      mimeType: 'application/json',
      buffer: Buffer.from(
        JSON.stringify({
          id: 'check@1',
          argv: ['python', '-m', 'unittest'],
          protected_tests: { 'tests/check.py': 'assert 2 + 3 == 5' },
        }),
      ),
    });
    await dialog.getByRole('button', { name: '上传并预检' }).click();
    await expect(dialog.getByLabel('项目 ID', { exact: true })).toBeDisabled();
    await expect(dialog.getByLabel('完整 Git commit')).toBeDisabled();
    await expect(dialog.getByLabel('验收与依赖 JSON')).toBeDisabled();
    releasePreflight();
    await expect(dialog.getByRole('status')).toContainText('尚未执行独立验收');
    await dialog.getByRole('button', { name: '登记已预检项目' }).click();
    await page
      .getByRole('dialog', { name: '项目已接入' })
      .getByRole('button', { name: '用这个项目开始讨论' })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('combobox', { name: '本次任务的项目' })).toHaveValue('demo');
    if (entry === 'settings') await expect(page).toHaveURL(/\?project=demo$/);
    else {
      await page.reload();
      await expect(composer).toHaveValue('接入后，请检查我的项目');
      await expect(page.getByRole('combobox', { name: '本次任务的项目' })).toHaveValue('demo');
    }
    expect(registered).toBe(true);
  });
}
