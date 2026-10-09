import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AppController, Page } from './types';
import { stamp, type WorkspaceState } from './runtime';
import { api } from './api';

type Route={page:Page;id:string;tab:string;project:string;q:string;filter:string};
export type TaskDraft={title:string;description:string;criteria:string;scope:string;project:string;model:string;budget:number;skills:string[];allowedPaths?:string;allowExternal?:boolean;allowExternalRead?:boolean;connections?:string[];allowDelegation?:boolean};
export type Scenario='active'|'approval'|'recovery'|'unknown'|'tool'|'child'|'retry'|'queued'|'failure';
const empty:WorkspaceState={schema:2,runs:[],approvals:[],artifacts:[],skills:[],memories:[],events:[],settings:{budget:5,concurrency:2,notifications:true,redact:true},settings_revision:1,evalCompleted:false,projects:[]};
function readRoute():Route {
 const q=new URLSearchParams(location.search);const path=location.pathname.split('/').filter(Boolean);
 const known:Page[]=['overview','runs','inbox','approvals','recovery','artifacts','context','skills','memory','tools','evaluations','observability','settings'];
 const id=path[0]==='tasks'?decodeURIComponent(path[1]||''):q.get('run')||'';
 return {page:id?'task':known.includes(path[0] as Page)?path[0] as Page:'overview',id,tab:q.get('view')||'timeline',project:q.get('project')||'所有项目',q:q.get('q')||'',filter:q.get('status')||'all'};
}
function url(r:Route){const q=new URLSearchParams();if(r.project!=='所有项目')q.set('project',r.project);if(r.q)q.set('q',r.q);if(r.filter!=='all')q.set('status',r.filter);if(r.tab!=='timeline')q.set('view',r.tab);return `${r.page==='task'?`/tasks/${encodeURIComponent(r.id)}`:r.page==='overview'?'/':`/${r.page}`}${q.size?'?'+q:''}`}
export function useRuntime(){
 const [state,setState]=useState<WorkspaceState>(empty);const [route,setRoute]=useState(readRoute);
 const [overlay,setOverlay]=useState<'create'|'command'|'notifications'|null>(null);
 const [toast,setToast]=useState('');const [now,setNow]=useState(stamp);const [evalRunning,setEvalRunning]=useState(false);
 const [storageError,setStorageError]=useState('');const [loading,setLoading]=useState(true);
 const restore=useRef<number|null>(null);const stateRef=useRef(state);stateRef.current=state;
 const pending=useRef(new Set<string>());const requestSequence=useRef(0);
 async function refresh(){const seq=++requestSequence.current;try{const next=await api<WorkspaceState>('/workspace');if(seq===requestSequence.current){setState(next);setStorageError('');setLoading(false)}}catch(e){if(seq===requestSequence.current){setStorageError(`后端连接失败：${(e as Error).message}`);setLoading(false)}}}
 useEffect(()=>{let alive=true;let timer:ReturnType<typeof setTimeout>;const poll=async()=>{await refresh();if(alive)timer=setTimeout(poll,1500)};void poll();return()=>{alive=false;clearTimeout(timer);requestSequence.current++}},[]);
 useEffect(()=>{const timer=setInterval(()=>setNow(stamp()),1000);return()=>clearInterval(timer)},[]);
 useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(''),5000);return()=>clearTimeout(id)},[toast]);
 useEffect(()=>{history.replaceState({...history.state,forgeDepth:history.state?.forgeDepth||0},'',url(route));const pop=()=>{restore.current=history.state?.scroll||0;setRoute(readRoute());setOverlay(null)};window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop)},[]);
 useLayoutEffect(()=>{if(restore.current!==null){window.scrollTo({top:restore.current,behavior:'instant'});restore.current=null}},[route]);
 function changeRoute(next:Route,replace=false){if(!replace){history.replaceState({...history.state,scroll:window.scrollY},'');history.pushState({forgeDepth:(history.state?.forgeDepth||0)+1,scroll:0},'',url(next));restore.current=0}else history.replaceState(history.state,'',url(next));setRoute(next);setOverlay(null)}
 const navigate=(page:Page)=>changeRoute({...route,page,id:'',q:'',filter:'all',tab:'timeline'});
 const openRun=(id:string)=>changeRoute({...route,page:'task',id,tab:'timeline'});
 const back=()=>{if(history.state?.forgeDepth>0)history.back();else navigate('runs')};
 async function mutate(path:string,method='POST',body?:unknown){if(pending.current.has(path))return;pending.current.add(path);try{await api(path,method,body);await refresh()}catch(e){setToast((e as Error).message)}finally{pending.current.delete(path)}}
 const version=(id:string)=>stateRef.current.runs.find(r=>r.id===id)?.stateVersion;
 function updateRun(id:string,action:'pause'|'resume'|'cancel'|'complete'|'recover'){
  if(action==='complete'){verify(id,true);return}
  const r=stateRef.current.runs.find(r=>r.id===id);if(!r)return;
  if(r.status==='FAILED'&&(action==='recover'||action==='resume')){void fork(id);return}
  void mutate(`/runs/${id}/${action==='recover'?'resume':action}`,'POST',{expected_version:version(id),reason:'用户通过控制台请求'+action});
 }
 function verify(id:string,_passed:boolean){void mutate(`/runs/${id}/verify`,'POST',{expected_version:version(id),reason:'用户请求独立验收'})}
 async function fork(id:string){try{const next=await api(`/runs/${id}/fork`,'POST',{expected_version:version(id),reason:'基于既有工作区创建新尝试'},crypto.randomUUID());await refresh();openRun(next.id)}catch(e){setToast((e as Error).message)}}
 async function decide(id:string,decision:'approved'|'denied',reason=''):Promise<string|null>{
  const a=stateRef.current.approvals.find(a=>a.id===id);if(!a)return '审批记录不可用';
  if(pending.current.has(id))return '正在提交';pending.current.add(id);
  try{await api(`/approvals/${id}/decision`,'POST',{expected_version:version(a.runId),decision:decision==='approved'?'approve':'deny',effect_digest:a.digest,reason:reason||'用户批准当前明确操作'});await refresh();return null}catch(e){return (e as Error).message}finally{pending.current.delete(id)}
 }
 async function create(d:TaskDraft){
  const r=await api('/runs','POST',{project_id:d.project,title:d.title,task:{goal:d.description,criteria:d.criteria.split('\n').filter(Boolean),scope:d.scope,allowed_paths:(d.allowedPaths||'src,tests').split(',').map(s=>s.trim()).filter(Boolean)},budget:{max_cost_usd:String(d.budget)},model:d.model==='fixture'?'fixture':'configured',skills:d.skills,connections:d.connections||[],capabilities:['repo.read','workspace.write','tests.run',...(d.allowExternalRead?['external.read']:[]),...(d.allowExternal?['external.write']:[]),...(d.allowDelegation?['delegate']:[])]},crypto.randomUUID());
  await refresh();changeRoute({...route,page:'task',id:r.id,project:d.project,tab:'timeline'});return r.id;
 }
 async function scenario(_kind:Scenario='active'){try{const r=await api('/examples/smoke','POST',undefined,crypto.randomUUID());await refresh();openRun(r.id)}catch(e){setToast((e as Error).message)}}
 async function reconcile(id:string,_occurred:boolean){openRun(id);setToast('请在动作账本中填写对账证据并提交处置。')}
 const unavailable=()=>setToast('请在工作空间设置修复实际配置后恢复；后端不会模拟修改执行条件。');
 function repair(id:string,kind:string){if(kind==='version'){void fork(id);return}if(kind==='permission'){navigate('settings');return}void mutate(`/runs/${id}/recheck`,'POST',{expected_version:version(id),reason:'用户重新检查实际沙箱环境'})}
 function requestApproval(id:string){void mutate(`/runs/${id}/reapprove`,'POST',{expected_version:version(id),reason:'用户请求对当前操作重新审查'})}
 const download=(name:string,content:string)=>{const u=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
 const app:AppController={variant:'twitter',page:route.page,navigate,runs:state.runs,approvals:state.approvals,artifacts:state.artifacts,skills:state.skills,memories:state.memories,project:route.project,
  setProject:p=>changeRoute({...route,project:p,page:route.page==='task'?'overview':route.page,id:'',q:'',filter:'all'}),search:route.q,setSearch:q=>changeRoute({...route,q},true),selectedRun:state.runs.find(r=>r.id===route.id)||null,
  openRun,closeRun:back,newRun:()=>setOverlay('create'),updateRun,decideApproval:(id,d)=>{void decide(id,d).then(e=>{if(e)setToast(e)})},notify:setToast,command:()=>setOverlay('command'),showNotifications:()=>setOverlay('notifications'),download,
  verifyArtifact:id=>{const a=state.artifacts.find(a=>a.id===id);if(a)openRun(a.runId)},
  toggleSkill:id=>{const skill=state.skills.find(x=>x.id===id);void mutate(`/skills/${encodeURIComponent(id)}/release`,'POST',{enabled:!skill?.enabled,review:'用户在控制台确认技能发布状态'})},
  addMemory:(title,content,kind)=>{void mutate('/memories','POST',{title,content,kind,source:'用户在控制台确认',project:route.project==='所有项目'?null:route.project})},removeMemory:id=>{void mutate(`/memories/${id}`,'DELETE')},
  settings:state.settings,settingsReady:!loading&&!storageError,settingsRevision:state.settings_revision,
  saveSettings:async(settings,revision)=>{try{await api('/settings','PUT',{...settings,expected_revision:revision});await refresh();setToast('设置已保存');return true}catch(e){setToast((e as Error).message);return false}},
  reset:()=>setToast('真实执行记录保留审计，不提供演示重置。'),evalCompleted:state.evalCompleted,evalRunning,
  runEval:()=>{setEvalRunning(true);void mutate('/evaluations','POST',{repetitions:3,seed:42}).finally(()=>setEvalRunning(false))}};
 return {app,state,route,overlay,setOverlay,toast,now,storageError,loading,back,create,scenario,verify,decide,repair,reconcile,revise:(_id:string)=>unavailable(),requestApproval,receive:(_id:string)=>unavailable(),emitUpdate:(_id:string)=>refresh(),assignResource:(_id:string)=>refresh(),refresh,fork,
  clearFilters:()=>changeRoute({...route,q:'',filter:'all'},true),openTask:(id:string,tab='timeline')=>changeRoute({...route,page:'task',id,tab}),setFilter:(filter:string)=>changeRoute({...route,filter},true),setTab:(tab:string)=>changeRoute({...route,tab},true)};
}
export type Runtime=ReturnType<typeof useRuntime>;
