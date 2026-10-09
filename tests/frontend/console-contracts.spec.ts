import {createHash} from 'node:crypto';
import {test,expect} from '@playwright/test';

test('uncertain writes preserve their idempotency key across reload and retain structured errors',async({page})=>{
 const keys:string[]=[];let fail=true;
 await page.route('**/v1/runs',async route=>{if(route.request().method()!=='POST'){await route.continue();return}keys.push(route.request().headers()['idempotency-key']);if(fail){fail=false;await route.abort('connectionreset')}else await route.fulfill({status:202,json:{id:'stable-result'}})});
 await page.goto('/');
 const first=await page.evaluate(async()=>{const {api}=await import('/src/api.ts');try{await api('/runs','POST',{test:'uncertain'})}catch(error:any){return {code:error.payload.code,uncertain:error.uncertain}}});
 expect(first).toEqual({code:'NETWORK_ERROR',uncertain:true});
 await page.reload();
 expect(await page.evaluate(async()=>{const {api}=await import('/src/api.ts');return await api('/runs','POST',{test:'uncertain'})})).toEqual({id:'stable-result'});
 expect(keys).toHaveLength(2);expect(keys[0]).toBe(keys[1]);
 await page.route('**/v1/test-error',route=>route.fulfill({status:409,json:{code:'VERSION_CONFLICT',message:'changed',correlation_id:'trace-123',retryable:false}}));
 const error=await page.evaluate(async()=>{const {api}=await import('/src/api.ts');try{await api('/test-error')}catch(error:any){return {status:error.status,payload:error.payload}}});
 expect(error).toEqual({status:409,payload:{code:'VERSION_CONFLICT',message:'changed',correlation_id:'trace-123',retryable:false}});
});

test('connection configuration opens a validated form with optional advanced JSON',async({page})=>{
 await page.goto('/tools');await page.getByRole('button',{name:'登记连接',exact:true}).click();
 const form=page.getByRole('dialog',{name:'登记外部连接',exact:true});
 await expect(form.getByLabel('服务地址',{exact:true})).toHaveAttribute('type','url');
 await expect(form.getByLabel('名称',{exact:true})).toBeVisible();
 await expect(form.getByRole('textbox',{name:'配置内容'})).toHaveCount(0);
 await form.getByRole('button',{name:'高级 JSON 配置'}).click();
 await expect(form.getByRole('textbox',{name:'配置内容'})).toBeVisible();
});

test('requests support explicit cancellation without discarding uncertain write facts',async({page})=>{
 await page.goto('/');
 const result=await page.evaluate(async()=>{const {api}=await import('/src/api.ts');const controller=new AbortController();controller.abort();try{await api('/runs','POST',{test:'cancel'},undefined,{signal:controller.signal})}catch(error:any){return {code:error.payload.code,uncertain:error.uncertain}}});
 expect(result).toEqual({code:'REQUEST_CANCELLED',uncertain:true});
});

test('OIDC mode requires the identity provider instead of a manual access token',async({page})=>{
 await page.route('**/v1/auth/config',route=>route.fulfill({json:{mode:'oidc',authority:'https://identity.example.test',client_id:'forge-browser',scope:'openid profile'}}));
 await page.goto('/');
 await expect(page.getByRole('button',{name:'登录工作空间'})).toBeVisible();
 await expect(page.getByRole('textbox',{name:'OIDC 访问令牌'})).toHaveCount(0);
});


test('OIDC authorization code callback verifies PKCE and restores the task route',async({page})=>{
 const issuer='https://identity.example.test';let nonce='';let challenge='';let verifier='';
 await page.route('**/v1/auth/config',route=>route.fulfill({json:{mode:'oidc',authority:issuer,client_id:'forge-browser',scope:'openid profile'}}));
 await page.route(issuer+'/**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/.well-known/openid-configuration'){await route.fulfill({json:{issuer,authorization_endpoint:issuer+'/authorize',token_endpoint:issuer+'/token',jwks_uri:issuer+'/jwks',response_types_supported:['code'],subject_types_supported:['public'],id_token_signing_alg_values_supported:['RS256']}});return}
  if(url.pathname==='/authorize'){nonce=url.searchParams.get('nonce')||'';challenge=url.searchParams.get('code_challenge')||'';expect(url.searchParams.get('code_challenge_method')).toBe('S256');const callback=new URL(url.searchParams.get('redirect_uri')!);callback.searchParams.set('state',url.searchParams.get('state')!);callback.searchParams.set('code','fixture-code');await route.fulfill({status:302,headers:{location:callback.href}});return}
  if(url.pathname==='/token'){const form=new URLSearchParams(route.request().postData()!);verifier=form.get('code_verifier')||'';expect(form.get('code')).toBe('fixture-code');const now=Math.floor(Date.now()/1000);const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');const jwt=encode({alg:'RS256',typ:'JWT'})+'.'+encode({iss:issuer,aud:'forge-browser',sub:'tester',iat:now,exp:now+600,nonce})+'.fixture-signature';await route.fulfill({json:{access_token:'fixture-access',token_type:'Bearer',expires_in:600,id_token:jwt}});return}
  await route.abort();
 });
 await page.goto('/runs?q=preserved');await page.getByRole('button',{name:'登录工作空间'}).click();
 await expect(page.getByRole('button',{name:'退出登录'})).toBeVisible();
 await expect(page).toHaveURL(/runs\?q=preserved/);
 expect(verifier.length).toBeGreaterThanOrEqual(43);expect(createHash('sha256').update(verifier).digest('base64url')).toBe(challenge);
 expect(await page.evaluate(()=>sessionStorage.getItem('forge-access-token'))).toBe('fixture-access');
 await page.getByRole('button',{name:'退出登录'}).click();await expect(page.getByRole('button',{name:'登录工作空间'})).toBeVisible();
});
