import { useState } from 'react';
import { Activity, ArrowUpRight, Bell, BookOpen, ChevronDown, Command, FolderGit2, ForgeMark, Project, Home, Inbox, LayoutList, Menu, PanelRightClose, PanelRightOpen, Plus, Search, Settings, ShieldCheck, Workflow, X } from '../../icons';
import type { Page } from '../../types';
import type { Runtime } from '../../useRuntime';
import { needsAttention } from '../../runtime';
import { Button, Dialog } from '../../components';
import { ConsoleContent, Inspector } from '../../TaskViews';
import { ActionOverlays } from '../../Actions';
import './style.css';

const nav=[{page:'overview',label:'工作台',icon:Home},{page:'runs',label:'任务',icon:LayoutList},{page:'inbox',label:'待我处理',icon:Inbox},{page:'artifacts',label:'交付物',icon:FolderGit2},{page:'skills',label:'知识与能力',icon:BookOpen},{page:'observability',label:'运行分析',icon:Activity}] as const;
export default function Twitter({runtime:rt}:{runtime:Runtime}){
 const {app,state,route}=rt;const [menu,setMenu]=useState(false);const [info,setInfo]=useState(false);const [right,setRight]=useState(true);
 const allProjects=Array.from(new Set([...(state.projects||[]).map(p=>p.id),...state.runs.map(r=>r.project)]));
 const scoped=state.runs.filter(r=>route.project==='所有项目'||r.project===route.project);
 const attention=scoped.filter(needsAttention).length;
 const activePage=route.page==='task'?'runs':['approvals','recovery'].includes(route.page)?'inbox':['skills','memory','tools','context'].includes(route.page)?'skills':['evaluations','observability'].includes(route.page)?'observability':route.page;
 const go=(page:Page)=>{app.navigate(page);setMenu(false)};
 const navigation=<nav aria-label="主导航">{nav.map(({page,label,icon:Icon})=><button key={page} className={`nav-link ${activePage===page?'active':''}`} aria-current={activePage===page?'page':undefined} onClick={()=>go(page)}><Icon size={20}/><span>{label}</span>{page==='inbox'&&attention>0&&<b>{attention}</b>}</button>)}</nav>;
 const secondary=activePage==='skills'?[['skills','技能库'],['memory','项目记忆'],['tools','工具连接']]:activePage==='observability'?[['observability','运行观测'],['evaluations','评测实验']]:activePage==='inbox'?[['inbox','待处理'],['approvals','审批记录'],['recovery','恢复诊断']]:[];
 return <div className="variant twitter forge-console">
  <a className="skip-link" href="#main-content">跳到主要内容</a>
  <div className="console-viewport"><div className={`console-layout ${right?'':'inspector-hidden'}`}>
   <aside className="console-sidebar">
    <a className="brand" href="/" onClick={e=>{e.preventDefault();go('overview')}}><span><ForgeMark size={24}/></span>ForgeAgent<span className="brand-dot"/></a>
    <div className="workspace-picker"><div className="workspace-mark">F</div><div><strong>Forge Workspace</strong><small>个人工作空间</small></div></div>
    <label className="project-picker"><Project size={16}/><select aria-label="当前项目" value={route.project} onChange={e=>app.setProject(e.target.value)}><option>所有项目</option>{allProjects.map(p=><option key={p}>{p}</option>)}</select></label>
    <button className="new-task" onClick={app.newRun}><Plus size={18}/>创建任务<kbd>Alt N</kbd></button>
    {navigation}
    <div className="sidebar-bottom"><button className={`nav-link ${route.page==='settings'?'active':''}`} onClick={()=>go('settings')}><Settings size={20}/><span>工作空间设置</span></button><div className="demo-mode"><span/>持久运行时<small>PostgreSQL · 服务端保存</small></div></div>
   </aside>
   <div className="console-body">
    <header className="console-topbar"><div className="breadcrumb"><button className="icon-btn mobile-menu" aria-label="打开导航" onClick={()=>setMenu(true)}><Menu size={20}/></button><span className="breadcrumb-workspace">工作空间 <span>/</span></span><strong>{route.project==='所有项目'?'全部项目':route.project}</strong><span className="mode-label">{rt.loading?'正在连接':rt.storageError?'连接异常':'已连接后端'}</span></div><div className="header-actions"><button className="search-trigger" onClick={app.command}><Search size={16}/><span>搜索任务与页面</span><kbd>⌘ K</kbd></button><button className="icon-btn" aria-label="查看待办通知" onClick={app.showNotifications}><Bell size={18}/>{attention>0&&<i className="notification-dot"/>}</button><button className="icon-btn" aria-label={right?'收起辅助信息':'显示辅助信息'} onClick={()=>{if((document.querySelector('.console-viewport')?.clientWidth||innerWidth)<1280)setInfo(true);else setRight(!right)}}>{right?<PanelRightClose size={18}/>:<PanelRightOpen size={18}/>}</button></div></header>
    <div className="mobile-project"><label>当前项目<select aria-label="手机当前项目" value={route.project} onChange={e=>app.setProject(e.target.value)}><option>所有项目</option>{allProjects.map(p=><option key={p}>{p}</option>)}</select></label></div>
    {secondary.length>0&&<nav className="section-nav" aria-label="功能分组">{secondary.map(([page,label])=><button className={route.page===page?'active':''} key={page} onClick={()=>go(page as Page)}>{label}</button>)}</nav>}
    <main id="main-content" className="console-main" tabIndex={-1}>
     {rt.storageError&&<div className="notice warning" role="alert">{rt.storageError}<Button onClick={()=>app.download('forge-workspace.json',JSON.stringify(state,null,2))}>导出工作空间</Button></div>}
     <ConsoleContent runtime={rt}/>
    </main>
   </div>
   <aside className="console-inspector" aria-label="辅助信息"><Inspector runtime={rt}/></aside>
  </div>
  </div><nav className="mobile-dock" aria-label="手机快捷导航"><button aria-label="工作台" onClick={()=>go('overview')}><Home size={21}/><span>工作台</span></button><button aria-label="任务" onClick={()=>go('runs')}><LayoutList size={21}/><span>任务</span></button><button className="dock-create" aria-label="创建任务" onClick={app.newRun}><Plus size={23}/></button><button aria-label="待我处理" onClick={()=>go('inbox')}><Inbox size={21}/><span>待处理{attention>0?` ${attention}`:''}</span></button><button aria-label="更多功能" onClick={()=>setMenu(true)}><Menu size={21}/><span>更多</span></button></nav>
  {menu&&<Dialog title="工作空间导航" onClose={()=>setMenu(false)}>{navigation}<Button onClick={()=>go('settings')}>工作空间设置</Button></Dialog>}
  {info&&<Dialog title="辅助信息" onClose={()=>setInfo(false)}><Inspector runtime={rt}/></Dialog>}
  <ActionOverlays runtime={rt}/>
  {rt.toast&&<div className="toast" role="status"><ShieldCheck size={17}/>{rt.toast}</div>}
 </div>
}

