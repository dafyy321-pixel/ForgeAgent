import {test,expect} from '@playwright/test';

const workspace={schema:2,runs:[],approvals:[],artifacts:[],skills:[],memories:[],events:[],
 settings:{budget:19,concurrency:3,notifications:true,redact:true},settings_revision:1,evalCompleted:false,projects:[]};

test('settings wait for async data, preserve dirty drafts and reject stale saves',async({page})=>{
 let current=structuredClone(workspace);let lastBody:any;
 await page.route('**/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/v1/auth/config'){await route.fulfill({json:{mode:'local'}});return}
  if(path==='/v1/workspace/revision'){await route.fulfill({json:{revision:String(Date.now())}});return}
  if(path==='/v1/workspace'){await new Promise(resolve=>setTimeout(resolve,400));await route.fulfill({json:current});return}
  if(path==='/v1/settings'){
   lastBody=route.request().postDataJSON();
   if(lastBody.expected_revision!==current.settings_revision){await route.fulfill({status:409,json:{code:'SETTINGS_CONFLICT',message:'Settings changed; reload before saving your draft'}});return}
   const {expected_revision,...settings}=lastBody;current={...current,settings,settings_revision:expected_revision+1};
   await route.fulfill({json:settings});return;
  }
  await route.fulfill({json:path.startsWith('/v1/catalog/')?{items:[],next_cursor:null}:[]});
 });
 await page.goto('/settings');
 const budget=page.getByLabel('默认任务预算（USD）');
 await expect(budget).toBeDisabled();
 await expect(budget).toHaveValue('19');
 await expect(budget).toBeEnabled();
 await budget.fill('25');
 current={...current,settings:{...current.settings,budget:21},settings_revision:2};
 await expect(page.getByRole('alert')).toContainText('服务端设置已变更');
 await expect(budget).toHaveValue('25');
 await page.getByRole('button',{name:'保存设置',exact:true}).click();
 await expect(page.getByText('SETTINGS_CONFLICT: Settings changed; reload before saving your draft')).toBeVisible();
 expect(lastBody.expected_revision).toBe(1);
 await expect(budget).toHaveValue('25');
 await page.getByRole('button',{name:'重新加载设置'}).click();
 await expect(budget).toHaveValue('21');
 await budget.fill('22');
 await page.getByRole('button',{name:'保存设置',exact:true}).click();
 await expect(page.getByText('设置已保存', {exact:true})).toBeVisible();
 expect(lastBody.expected_revision).toBe(2);
 expect(current.settings.budget).toBe(22);
});

test('skill release selects eligible evidence and submits the reviewed configuration',async({page})=>{
 const skill={id:'debugging@1.0.0',name:'debugging',description:'Debug safely',version:'1.0.0',enabled:false,calls:0,category:'开发'};
 let releaseBody:any;
 await page.route('**/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/v1/auth/config'){await route.fulfill({json:{mode:'local'}});return}
  if(path==='/v1/workspace/revision'){await route.fulfill({json:{revision:String(Date.now())}});return}
  if(path==='/v1/workspace'){await route.fulfill({json:{...workspace,skills:[skill]}});return}
  if(path==='/v1/catalog/skills'){await route.fulfill({json:{items:[skill],next_cursor:null}});return}
  if(path.endsWith('/release-options')){await route.fulfill({json:[
   {evaluation_id:'invalid-experiment',configuration:'candidate',eligible:false,reason:'Cost gate failed'},
   {evaluation_id:'held-out-experiment',configuration:'skill-only',eligible:true,independent_cases:20,repetitions:3,comparison:{cost_ratio:0.8}}
  ]});return}
  if(path.endsWith('/release')){releaseBody=route.request().postDataJSON();skill.enabled=true;await route.fulfill({json:{status:'active'}});return}
  await route.fulfill({json:path.startsWith('/v1/catalog/')?{items:[],next_cursor:null}:[]});
 });
 await page.goto('/skills');
 await page.getByRole('switch',{name:'启用 debugging'}).click();
 const form=page.getByRole('dialog',{name:'审核并发布技能版本'});
 await expect(form.getByRole('button',{name:'发布技能'})).toBeDisabled();
 await expect(form).toContainText('Cost gate failed');
 await form.getByLabel('合格评测证据').selectOption('held-out-experiment/skill-only');
 await form.getByLabel('审核说明').fill('审核完成，费用和权限边界已确认');
 await form.getByRole('button',{name:'发布技能'}).click();
 await expect(page.getByRole('switch',{name:'停用 debugging'})).toBeVisible();
 expect(releaseBody).toMatchObject({enabled:true,evaluation_id:'held-out-experiment',configuration:'skill-only'});
});
