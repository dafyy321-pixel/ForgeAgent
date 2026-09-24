import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { useRuntime } from './useRuntime';
import Twitter from './variants/twitter';
import './base.css';

function Workspace() {
 const runtime = useRuntime();
 useEffect(() => {
  document.title = 'ForgeAgent · 工作空间';
  const listener = (e:KeyboardEvent) => {
   const dialog = document.querySelector('[role="dialog"]');
   if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && (!dialog || runtime.overlay === 'command')) {
    e.preventDefault(); runtime.setOverlay(runtime.overlay === 'command' ? null : 'command');
   }
   if (e.altKey && e.key.toLowerCase() === 'n' && !dialog) {e.preventDefault();runtime.setOverlay('create')}
  };
  document.addEventListener('keydown',listener);
  return () => document.removeEventListener('keydown',listener);
 },[runtime.overlay]);
 return <Twitter runtime={runtime}/>;
}
createRoot(document.getElementById('root')!).render(<Workspace/>);
