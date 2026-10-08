import type { Run, Approval, Artifact, Skill, Memory, AppController } from './types';

export type WaitReason = 'APPROVAL'|'TOOL'|'CHILD_RUN'|'RETRY_TIMER'|'RECONCILIATION'|null;
export type EventKind = 'task'|'plan'|'tool'|'approval'|'recovery'|'verification'|'cancel';
export interface RunEvent { id:string; runId:string; time:number; kind:EventKind; title:string; detail:string; }
export interface Execution extends Run {
  stateVersion?:number;inputRequired?:boolean;
  updatedAt:number; waitReason:WaitReason; scope:string; criteria:string[];
  milestones:{label:string;done:boolean}[];
  snapshot:{model:string;skills:{name:string;version:string}[];memories:Memory[];time:number};
  checkpoint:{id:string;workspace:boolean;compatible:boolean;permission:boolean;environment:boolean};
  unknownEffect:boolean; cancelRequested:boolean; version:number; deadline:number|null; recoveryPending?:boolean;
  verification:{status:'not_run'|'running'|'passed'|'failed';version:number;checks:{name:string;status:'passed'|'failed'|'not_run'|'not_applicable';evidence:string}[]};
}
export interface Decision extends Approval { expiresAt:number; version:number; reason:string; impact:string; diff:string; submission?:{decision:'approved'|'denied';reason:string;due:number}; }
export interface Evidence extends Artifact { version:number;digest:string; }
export interface WorkspaceState {
  projects?:{id:string;name:string;acceptance?:string}[];
  schema:2;runs:Execution[];approvals:Decision[];artifacts:Evidence[];skills:Skill[];memories:(Memory & {project?:string})[];
  events:RunEvent[];settings:AppController['settings'];settings_revision:number;evalCompleted:boolean;
}
export const waitLabels:Record<NonNullable<WaitReason>,string> = {APPROVAL:'等待授权审批',TOOL:'等待远程工具返回',CHILD_RUN:'等待子任务结果',RETRY_TIMER:'等待重试时间',RECONCILIATION:'外部操作结果待确认'};
export const statusText:Record<Run['status'],string> = {QUEUED:'排队中',ACTIVE:'运行中',WAITING:'等待中',PAUSED:'已暂停',CANCELLING:'正在取消',SUCCEEDED:'已验证完成',FAILED:'执行失败',CANCELLED:'已取消'};
export const projects=['forge-api','console-web','data-pipeline','search-service'];
export function stamp(){return Date.now()}
export function event(runId:string,kind:EventKind,title:string,detail:string,time=stamp()):RunEvent{return{id:crypto.randomUUID(),runId,kind,title,detail,time}}
export function needsAttention(r:Execution){return ['PAUSED','FAILED'].includes(r.status)||(r.status==='WAITING'&&['APPROVAL','RECONCILIATION'].includes(r.waitReason||''))}
export function checksFor(r:Execution,approvals:Decision[]){return [
 {name:'工作区与检查点可用',ok:r.checkpoint.workspace,detail:r.checkpoint.id},
 {name:'执行版本兼容',ok:r.checkpoint.compatible,detail:r.checkpoint.compatible?'与保存时配置一致':'模型或工具版本不兼容，恢复已阻止'},
 {name:'当前权限有效',ok:r.checkpoint.permission&&!approvals.some(a=>a.runId===r.id&&a.status==='pending'),detail:'恢复不复用失效授权'},
 {name:'执行环境就绪',ok:r.checkpoint.environment,detail:r.checkpoint.environment?'依赖和沙箱检查通过':'请检查实际 Docker 沙箱与依赖配置'},
 {name:'剩余预算充足',ok:r.cost<r.budget,detail:`剩余 $${Math.max(0,r.budget-r.cost).toFixed(2)}`},
 {name:'外部效果已确认',ok:!r.unknownEffect,detail:r.unknownEffect?'响应丢失，禁止盲目重发':'没有待确认外部操作'}
 ]}
export function relativeTime(time:number){const d=Math.max(0,Math.floor((Date.now()-time)/60000));return d<1?'刚刚':d<60?`${d} 分钟前`:`${Math.floor(d/60)} 小时前`}
