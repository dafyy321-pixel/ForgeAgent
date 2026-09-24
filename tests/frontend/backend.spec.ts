import {test,expect} from '@playwright/test';

test('durable task completes and opens in a separate browser context',async({page,browser},testInfo)=>{
 await page.goto('/');
 await page.getByRole('button',{name:'运行契约示例',exact:true}).click();
 await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/);
 const taskUrl=page.url();
 await expect(page.locator('#main-content .run-badge').first()).toHaveText('已验证完成',{timeout:30000});
 await page.getByRole('navigation',{name:'任务工作区'}).getByRole('button',{name:'动作账本',exact:true}).click();
 await expect(page.locator('#main-content')).toContainText('repo.write · SUCCEEDED');
 await page.getByRole('navigation',{name:'任务工作区'}).getByRole('button',{name:'验收结果',exact:true}).click();
 await expect(page.getByRole('heading',{name:'验收通过，可交付'})).toBeVisible();
 await page.screenshot({path:testInfo.outputPath('task-verification.png'),fullPage:true});
 const context=await browser.newContext();const other=await context.newPage();await other.goto(taskUrl);
 await expect(other.locator('#main-content .run-badge').first()).toHaveText('已验证完成');
 await context.close();
});

test('create real-model task persists and reports missing configuration',async({page})=>{
 await page.goto('/');await page.keyboard.press('Alt+n');
 const form=page.getByRole('dialog',{name:'创建任务',exact:true});
 await form.getByLabel('任务目标',{exact:true}).fill('API integration task '+Date.now());
 await form.getByLabel('任务说明',{exact:true}).fill('检查注册项目中的代码并提出符合验收契约的修改。');
 await form.getByRole('button',{name:'创建任务',exact:true}).click();
 await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/);
 await expect(page.locator('#main-content .run-badge').first()).toHaveText('已暂停',{timeout:20000});
 await expect(page.locator('#main-content')).toContainText('MODEL_NOT_CONFIGURED');
 await page.reload();await expect(page.locator('#main-content .run-badge').first()).toHaveText('已暂停');
});

test('all retained pages and mobile widths render without errors',async({page},testInfo)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 for(const width of [1440,720,390]){
  await page.setViewportSize({width,height:900});
  for(const path of ['','runs','inbox','artifacts','skills','memory','tools','evaluations','observability','settings']){
   await page.goto('/'+path);
   await expect(page.locator('#main-content h1')).toHaveCount(1);
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${path} at ${width}`).toBeTruthy();
   if((path===''||path==='evaluations')&&(width===1440||width===390))await page.screenshot({path:testInfo.outputPath(`${path||'overview'}-${width}.png`),fullPage:true});
  }
 }
 expect(errors).toEqual([]);
});

test('paired evaluation forms persist cases and complete on the worker',async({page})=>{
 await page.goto('/evaluations');
 await page.getByRole('button',{name:'登记任务集',exact:true}).click();
 const name='ui-cases-'+Date.now()+'@1';
 await page.getByRole('textbox',{name:'配置内容'}).fill(JSON.stringify({id:name,source:'UI test',cases:[{id:'addition',project_id:'runtime-lab',task:{goal:'Fix add',allowed_paths:['src']},budget:{max_cost_usd:'0.10'}}]}));
 await page.getByRole('button',{name:'保存到服务端'}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.locator('#main-content')).toContainText(name);
 await page.getByRole('button',{name:'新建配对实验'}).click();
 await page.getByRole('textbox',{name:'配置内容'}).fill(JSON.stringify({dataset_id:name,model:'fixture',configurations:[{name:'baseline'},{name:'candidate',harness:{memory:false}}],repetitions:1,max_total_cost_usd:'0.20'}));
 await page.getByRole('button',{name:'保存到服务端'}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.locator('#main-content')).toContainText(`${name} · completed`,{timeout:30000});
});

test('project memory is saved on the server',async({page})=>{
 await page.goto('/memory');await page.getByRole('button',{name:'添加记忆',exact:true}).click();
 const dialog=page.getByRole('dialog');const title='Backend persistence '+Date.now();
 await dialog.getByLabel('标题',{exact:true}).fill(title);await dialog.getByLabel('内容',{exact:true}).fill('此记录由真实 API 写入 PostgreSQL。');
 await dialog.getByRole('button',{name:'保存记忆',exact:true}).click();
 await expect(page.locator('#main-content')).toContainText(title);
 await page.reload();await expect(page.locator('#main-content')).toContainText(title);
});
