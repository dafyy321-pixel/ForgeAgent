import type { ReactNode } from 'react';
export type Page = 'overview'|'runs'|'inbox'|'task'|'approvals'|'recovery'|'artifacts'|'context'|'skills'|'memory'|'tools'|'evaluations'|'observability'|'settings';
export type RunStatus = 'QUEUED'|'ACTIVE'|'WAITING'|'PAUSED'|'CANCELLING'|'SUCCEEDED'|'FAILED'|'CANCELLED';
export interface Run { id:string; title:string; project:string; status:RunStatus; phase:string; progress:number; model:string; cost:number; tokens:number; started:string; duration:string; steps:number; budget:number; description:string; }
export interface Approval { id:string; runId:string; title:string; tool:string; target:string; risk:string; status:'pending'|'approved'|'denied'|'expired'; digest:string; }
export interface Artifact { id:string; runId:string; name:string; type:string; size:string; verified:boolean; content:string; }
export interface Skill { content?:string; id:string; name:string; description:string; version:string; enabled:boolean; calls:number; category:string; }
export interface Memory { id:string; title:string; content:string; kind:string; source:string; }
export interface AppController {
 variant:string; page:Page; navigate:(page:Page)=>void; runs:Run[]; approvals:Approval[]; artifacts:Artifact[]; skills:Skill[]; memories:Memory[];
 search:string; setSearch:(s:string)=>void; project:string; setProject:(s:string)=>void;
 selectedRun:Run|null; openRun:(id:string)=>void; closeRun:()=>void; newRun:()=>void;
 updateRun:(id:string,action:'pause'|'resume'|'cancel'|'complete'|'recover')=>void;
 decideApproval:(id:string,decision:'approved'|'denied')=>void; toggleSkill:(id:string)=>void;
 notify:(message:string)=>void; command:()=>void; showNotifications:()=>void;
 download:(name:string,content:string)=>void; verifyArtifact:(id:string)=>void;
 addMemory:(title:string,content:string,kind:string)=>void; removeMemory:(id:string)=>void;
 settings:{budget:number;concurrency:number;notifications:boolean;redact:boolean};
 settingsReady:boolean;settingsRevision:number;
 saveSettings:(s:AppController['settings'],revision:number)=>Promise<boolean>;
 reset:()=>void; evalRunning:boolean; evalCompleted:boolean; runEval:()=>void;
}
export interface VariantProps { app:AppController; children:ReactNode }
export const pageLabels:Record<Page,string> = {overview:'工作台',runs:'任务',inbox:'待我处理',task:'任务工作区',approvals:'审批记录',recovery:'恢复诊断',artifacts:'交付物',context:'上下文检查',skills:'技能库',memory:'项目记忆',tools:'工具连接',evaluations:'评测实验',observability:'运行观测',settings:'工作空间设置'};
