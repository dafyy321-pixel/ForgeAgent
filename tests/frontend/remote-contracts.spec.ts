import {test,expect} from '@playwright/test';

test('remote connection discovery and task selection use the reviewed server catalog',async({page})=>{
 const workspace={schema:2,runs:[],approvals:[],artifacts:[],skills:[],memories:[],events:[],settings:{budget:5,concurrency:2,notifications:true,redact:true},settings_revision:1,projects:[{id:'runtime-lab',name:'Runtime Lab'}]};
 let connection:any={id:'reviewed-connection',name:'Reviewed MCP',kind:'mcp',protocol:'2026-07-28',url:'https://example.com/mcp',status:'pending',tools:[{operation:'lookup',effect:'read'}]};
 let submitted:any;
 await page.route('**/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/v1/workspace'){await route.fulfill({json:workspace});return}
  if(path==='/v1/connections'){await route.fulfill({json:[connection]});return}
  if(path==='/v1/connections/reviewed-connection/discover'){connection={...connection,status:'active',negotiated:{time:'2026-10-09'}};await route.fulfill({json:connection});return}
  if(path==='/v1/runs'&&route.request().method()==='POST'){submitted=route.request().postDataJSON();await route.fulfill({status:202,json:{id:'new-remote-task'}});return}
  await route.fulfill({json:[]});
 });
 await page.goto('/tools');
 await expect(page.getByText('待协议发现与 schema 核对')).toBeVisible();
 await page.getByRole('button',{name:'发现并核对契约'}).click();
 await expect(page.getByText('已协商 · 1 个审核工具 · 2026-10-09')).toBeVisible();
 await page.keyboard.press('Alt+n');
 const form=page.getByRole('dialog',{name:'创建任务',exact:true});
 await form.getByLabel('任务目标',{exact:true}).fill('Use the reviewed remote catalog');
 await form.getByLabel('任务说明',{exact:true}).fill('Read the approved service and retain its evidence.');
 await form.getByRole('button',{name:/运行配置/}).click();
 await form.getByLabel('允许审核过的远程只读工具及产物下载').check();
 await form.getByLabel('Reviewed MCP · 2026-07-28').check();
 await form.getByRole('button',{name:'创建任务',exact:true}).click();
 await expect.poll(()=>submitted?.connections).toEqual(['reviewed-connection']);
 expect(submitted.capabilities).toContain('external.read');
 expect(submitted.capabilities).not.toContain('external.write');
});
