import {test,expect} from '@playwright/test';

test('repository wizard validates inputs, uploads raw bytes and requires reviewed preflight before registration',async({page})=>{
 let uploaded=false;let registered=false;
 await page.route('**/v1/repository-bundles/*',async route=>{expect(route.request().headers()['content-type']).toBe('application/octet-stream');expect(route.request().postDataBuffer()?.toString()).toBe('fixture-bundle');uploaded=true;await route.fulfill({json:{bundle_digest:'sha256:fixture',bundle_bytes:14}})});
 await page.route('**/v1/projects/preflight',async route=>{expect(uploaded).toBe(true);const data=route.request().postDataJSON();expect(data.repository.commit).toBe('a'.repeat(40));expect(data.repository.bundle_base64).toBeUndefined();await route.fulfill({json:{files:3,commit:data.repository.commit,baseline_digest:'sha256:reviewed',acceptance_execution:'not_run'}})});
 await page.route('**/v1/projects',async route=>{registered=true;await route.fulfill({status:201,json:{id:'demo'}})});
 await page.goto('/settings');await page.getByRole('button',{name:'接入 Git 仓库',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'接入 Git 仓库',exact:true});
 await expect(dialog.getByRole('button',{name:'登记已预检项目'})).toBeDisabled();
 await dialog.getByLabel('项目 ID',{exact:true}).fill('demo');await dialog.getByLabel('完整 Git commit').fill('a'.repeat(40));
 await dialog.getByLabel('Git bundle',{exact:true}).setInputFiles({name:'repo.bundle',mimeType:'application/octet-stream',buffer:Buffer.from('fixture-bundle')});
 await dialog.getByLabel('验收与依赖 JSON').setInputFiles({name:'acceptance.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({id:'check@1',argv:['python','-m','unittest']}))});
 await dialog.getByRole('button',{name:'上传并预检'}).click();await expect(dialog.getByRole('status')).toContainText('尚未执行独立验收');
 await dialog.getByRole('button',{name:'登记已预检项目'}).click();await expect(dialog).toHaveCount(0);expect(registered).toBe(true);
});
