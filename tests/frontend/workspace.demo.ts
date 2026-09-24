import {test,expect,type Page} from '@playwright/test';
import fs from 'node:fs';
const main=(p:Page)=>p.locator('#main-content');
async function scenario(page:Page,name:string){await page.goto('/');await main(page).getByRole('button',{name:'演示场景',exact:true}).click();await page.getByRole('dialog',{name:'选择演示场景'}).getByRole('button',{name:new RegExp(name)}).click();await expect(page).toHaveURL(/\/tasks\/RUN-/);}
async function tab(page:Page,label:string){await page.getByRole('navigation',{name:'任务工作区'}).getByRole('button',{name:label,exact:true}).click()}

test('all pages and responsive layouts have a single heading and no runtime errors',async({page})=>{
 test.setTimeout(90000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await expect(main(page).getByRole('heading',{level:1})).toHaveText('工作台');
 await expect(page.getByRole('navigation',{name:'主导航'}).getByRole('button')).toHaveCount(6);
 fs.mkdirSync('public/previews',{recursive:true});await page.screenshot({path:'public/previews/twitter.png',fullPage:false});
 for(const path of ['runs','inbox','approvals','recovery','artifacts','context','skills','memory','tools','evaluations','observability','settings','tasks/RUN-0841?view=review','tasks/RUN-0843?view=context']){
  await page.goto('/'+path);await expect(main(page).getByRole('heading',{level:1})).toHaveCount(1);
  await expect(main(page)).not.toContainText('undefined');
  if(path==='tasks/RUN-0841?view=review')await page.screenshot({path:'public/previews/task-review.png',fullPage:true});
  if(path==='tasks/RUN-0843?view=context')await page.screenshot({path:'public/previews/task-context.png',fullPage:true});
 }
 for(const width of [390,720,1024]){
  await page.setViewportSize({width,height:844});
  for(const path of ['','runs','inbox','artifacts','skills','memory','tools','evaluations','observability','settings','tasks/RUN-0841?view=review','tasks/RUN-0840?view=recovery','tasks/RUN-0839?view=verification','tasks/RUN-0843?view=context']){
   await page.goto('/'+path);await expect(main(page).getByRole('heading',{level:1})).toHaveCount(1);
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${width} ${path} overflow`).toBeTruthy();
  }
  await page.goto('/');await page.screenshot({path:`test-results/workbench-${width}.png`,fullPage:true});
 }
 expect(errors).toEqual([]);
});

test('approval expiry blocks submission, queued resources and clean cancellation settle',async({page})=>{
 await scenario(page,'授权审批');await tab(page,'审查操作');await page.clock.install();await page.clock.fastForward(16*60*1000);
 await expect(main(page).getByText('处理说明：授权有效期已结束')).toBeVisible();await expect(main(page).getByRole('button',{name:'批准当前操作'})).toHaveCount(0);await expect(main(page).locator('.run-badge').first()).toHaveText('已暂停');
 await scenario(page,'资源排队');await expect(main(page).locator('.run-badge').first()).toHaveText('排队中');await main(page).locator('summary').filter({hasText:'本地演示控制'}).click();await main(page).getByRole('button',{name:'模拟分配执行资源'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
 await main(page).getByLabel('更多任务操作').click();await main(page).getByRole('button',{name:'取消任务',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'确认取消',exact:true}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('正在取消');await page.clock.fastForward(2000);await expect(main(page).locator('.run-badge').first()).toHaveText('已取消');
});

test('approval and recovery submissions survive immediate navigation and reload',async({page})=>{
 await scenario(page,'授权审批');const first=page.url();await tab(page,'审查操作');await main(page).getByRole('button',{name:'批准当前操作'}).click();await tab(page,'执行记录');await page.reload();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
 await scenario(page,'中断恢复');await tab(page,'恢复诊断');await main(page).getByRole('button',{name:'模拟修复执行环境'}).click();await main(page).getByRole('button',{name:'检查并继续任务'}).click();await tab(page,'执行记录');await page.reload();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
});

test('task typography and controls remain usable at 200 percent zoom',async({page})=>{
 await page.goto('/tasks/RUN-0841?view=review');await page.evaluate(()=>{document.documentElement.style.zoom='2'});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
 await expect(page.locator('.console-inspector')).toBeHidden();
 expect((await main(page).boundingBox())!.width).toBeGreaterThan(600);
 await expect(main(page).getByRole('button',{name:'批准当前操作'})).toBeVisible();await main(page).getByLabel('决定说明（拒绝时必填）').fill('放大后也能完成审查');
 await page.screenshot({path:'test-results/review-zoom-200.png',fullPage:false});
});

test('create task validates fields, saves draft and opens an addressable workspace',async({page})=>{
 await page.goto('/?project=console-web');await page.keyboard.press('Alt+n');const form=page.getByRole('dialog',{name:'创建任务',exact:true});
 await form.getByRole('button',{name:'创建任务',exact:true}).click();await expect(form.getByText('请填写此项，任务需要明确的目标与边界。').first()).toBeVisible();
 await form.getByLabel('任务目标',{exact:true}).fill('验收新增任务');await form.getByLabel('任务说明',{exact:true}).fill('测试用户创建流程');await expect(form.getByLabel('项目',{exact:true})).toHaveValue('console-web');
 await form.getByRole('button',{name:'保存草稿并关闭'}).click();await page.reload();await page.keyboard.press('Alt+n');await expect(form.getByLabel('任务目标',{exact:true})).toHaveValue('验收新增任务');
 await form.getByRole('button',{name:'创建任务',exact:true}).click();await expect(main(page).getByRole('heading',{level:1})).toHaveText('验收新增任务');await expect(page.getByRole('dialog')).toHaveCount(0);await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
 const url=page.url();await page.reload();await expect(page).toHaveURL(url);await expect(main(page).getByRole('heading',{level:1})).toHaveText('验收新增任务');
});

test('pause, resume, failed verification, recovery and version-bound delivery',async({page})=>{
 await scenario(page,'正常执行');await main(page).getByRole('button',{name:'请求暂停'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('已暂停');
 await tab(page,'恢复诊断');await main(page).getByRole('button',{name:'检查并继续任务'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
 await tab(page,'验收结果');await main(page).locator('summary').filter({hasText:'验证失败演示'}).click();await main(page).getByRole('button',{name:'模拟回归测试失败'}).click();await expect(main(page).getByRole('heading',{name:'验收未通过，交付已阻止'})).toBeVisible();
 await tab(page,'恢复诊断');await main(page).getByRole('button',{name:'检查并继续任务'}).click();await tab(page,'验收结果');await main(page).getByRole('button',{name:'运行模拟验证'}).click();await expect(main(page).getByRole('heading',{name:'验收通过，可交付'})).toBeVisible();
 const download=page.waitForEvent('download');await main(page).locator('.evidence-row').filter({hasText:'verification.json'}).getByRole('button',{name:'下载',exact:true}).click();const file=await download;const data=JSON.parse(fs.readFileSync((await file.path())!,'utf8'));expect(data.demo).toBe(true);expect(data.verdict).toBe('PASS');expect(data.version).toBe(1);
});

test('approval refusal requires reason and changes invalidate authorization',async({page})=>{
 await scenario(page,'授权审批');await tab(page,'审查操作');await main(page).getByRole('button',{name:'拒绝操作'}).click();await expect(main(page).getByRole('alert')).toContainText('请填写拒绝原因');
 await main(page).getByLabel('决定说明（拒绝时必填）').fill('先补充测试');await main(page).getByRole('button',{name:'拒绝操作'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('已暂停');await expect(main(page).getByText('处理说明：先补充测试')).toBeVisible();
 await main(page).getByRole('button',{name:'重新发起当前版本审查'}).click();await main(page).locator('summary').filter({hasText:'审批失效演示'}).click();await main(page).getByRole('button',{name:'模拟目标版本变化'}).click();await expect(main(page).getByText('处理说明：操作目标版本发生变化')).toBeVisible();await expect(main(page).getByRole('button',{name:'批准当前操作'})).toHaveCount(0);
 await main(page).getByRole('button',{name:'重新发起当前版本审查'}).click();await main(page).getByRole('button',{name:'批准当前操作'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');await page.reload();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
});

test('cancel settles in-flight effects and unknown outcome requires reconciliation',async({page})=>{
 await scenario(page,'外部效果待确认');await main(page).getByLabel('更多任务操作').click();await main(page).getByRole('button',{name:'取消任务',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'确认取消',exact:true}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('正在取消');
 await page.reload();await expect(main(page).locator('.run-badge').first()).toHaveText('等待中');await tab(page,'恢复诊断');await expect(main(page).getByRole('button',{name:'检查并继续任务'})).toBeDisabled();
 await main(page).getByLabel('对账结果',{exact:true}).selectOption('occurred');await main(page).getByRole('button',{name:'记录对账结果'}).click();await page.getByRole('dialog').getByRole('button',{name:'确认对账',exact:true}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('已取消');await expect(main(page).getByRole('button',{name:'检查并继续任务'})).toBeDisabled();
});

test('recovery cannot continue until failed environment check is repaired',async({page})=>{
 await scenario(page,'中断恢复');await tab(page,'恢复诊断');await expect(main(page).getByRole('button',{name:'检查并继续任务'})).toBeDisabled();await main(page).getByRole('button',{name:'模拟修复执行环境'}).click();await expect(main(page).getByRole('button',{name:'检查并继续任务'})).toBeEnabled();await main(page).getByRole('button',{name:'检查并继续任务'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');
});

test('project scope, list filters and back navigation preserve context',async({page})=>{
 await page.goto('/runs?project=forge-api');await page.getByLabel('搜索任务',{exact:true}).fill('认证');await page.getByRole('tab',{name:'推进中'}).click();const url=page.url();const titles=await main(page).locator('.task-row-title>button').allTextContents();expect(titles.length).toBeGreaterThan(0);
 await main(page).locator('.task-row-title>button').first().click();await page.getByRole('button',{name:'返回任务列表'}).click();await expect(page).toHaveURL(url);await expect(page.getByLabel('搜索任务',{exact:true})).toHaveValue('认证');await expect(page.getByRole('tab',{name:'推进中'})).toHaveAttribute('aria-selected','true');
 await page.getByLabel('搜索任务',{exact:true}).fill('不存在xyz');await main(page).getByRole('button',{name:'清除筛选'}).click();await expect(page.getByLabel('搜索任务',{exact:true})).toHaveValue('');await expect(page.getByRole('tab',{name:'全部',exact:true})).toHaveAttribute('aria-selected','true');
 await page.getByLabel('当前项目',{exact:true}).selectOption('console-web');await expect(main(page).locator('.task-row')).toHaveCount(1);
});

test('events are buffered without auto-scrolling or collapsing expanded records',async({page})=>{
 await scenario(page,'正常执行');const record=main(page).locator('.event-row details').first();await record.locator('summary').click();await main(page).locator('summary').filter({hasText:'本地演示控制'}).click();const count=await main(page).locator('.event-row').count();await main(page).getByRole('button',{name:'模拟接收新事件'}).click();const scroll=await page.evaluate(()=>scrollY);await expect(main(page).getByRole('button',{name:'1 条新事件 · 点击查看'})).toBeVisible();expect(await main(page).locator('.event-row').count()).toBe(count);expect(Math.abs(await page.evaluate(()=>scrollY)-scroll)).toBeLessThan(5);
 await main(page).getByRole('button',{name:'1 条新事件 · 点击查看'}).click();await expect(main(page).locator('.event-row')).toHaveCount(count+1);expect(await main(page).locator('.event-row details[open]').count()).toBe(1);
});

test('locked context does not follow later global skill toggles and retains original observations',async({page})=>{
 await scenario(page,'正常执行');const taskUrl=page.url();await tab(page,'上下文');await main(page).getByRole('button',{name:'技能版本快照',exact:true}).click();const content=await main(page).locator('.source-detail pre').textContent();
 await page.goto('/skills');await main(page).getByRole('switch').first().click();await page.goto(taskUrl+'&view=context');await main(page).getByRole('button',{name:'技能版本快照',exact:true}).click();await expect(main(page).locator('.source-detail pre')).toHaveText(content!);
 await main(page).getByRole('button',{name:'模拟压缩观察'}).click();await main(page).locator('summary').filter({hasText:'对照原始观察'}).click();await expect(main(page).locator('.technical-details pre')).toContainText('任务契约已创建');
});

test('waiting reasons and explicit results never pretend to be approvals',async({page})=>{
 for(const name of ['等待工具','等待子任务','等待重试']){await scenario(page,name);await expect(main(page).locator('.run-badge').first()).toHaveText('等待中');await expect(page.getByRole('navigation',{name:'任务工作区'}).getByRole('button',{name:'审查操作'})).toHaveCount(0);await main(page).locator('summary').filter({hasText:'本地演示控制'}).click();await main(page).getByRole('button',{name:'模拟接收等待结果'}).click();await expect(main(page).locator('.run-badge').first()).toHaveText('运行中');}
});

test('mobile navigation, inspector and creation dialogs support focus and Escape',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/');await page.getByRole('button',{name:'更多功能'}).click();const dialog=page.getByRole('dialog',{name:'工作空间导航'});await expect(dialog).toBeVisible();await page.keyboard.press('Shift+Tab');expect(await dialog.evaluate(el=>el.contains(document.activeElement))).toBe(true);await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 await page.getByRole('button',{name:'更多功能'}).click();await page.getByRole('dialog').getByRole('button',{name:'知识与能力'}).click();await expect(main(page).getByRole('heading',{level:1})).toHaveText('技能库');
 await page.getByRole('button',{name:'收起辅助信息'}).click();await expect(page.getByRole('dialog',{name:'辅助信息'})).toBeVisible();await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'创建任务',exact:true}).click();await expect(page.getByRole('dialog',{name:'创建任务',exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();await page.screenshot({path:'test-results/create-mobile.png',fullPage:false,animations:'disabled'});
});

test('old links and storage remain compatible; missing task has a clear state',async({page})=>{
 await page.goto('/twitter?run=RUN-0842');await expect(page).toHaveURL(/\/tasks\/RUN-0842/);await expect(main(page).getByRole('heading',{level:1})).toHaveText('修复认证模块的并发刷新问题');
 await page.getByRole('button',{name:'关注任务',exact:true}).click();await page.goto('/runs?status=following');await expect(main(page).locator('.task-row')).toHaveCount(1);await page.reload();await expect(main(page).locator('.task-row')).toHaveCount(1);
 await page.goto('/tasks/no-such-task');await expect(main(page).getByRole('heading',{name:'此浏览器没有这条任务'})).toBeVisible();await expect(page.getByRole('dialog')).toHaveCount(0);
});

