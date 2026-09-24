import { spawn, spawnSync } from 'node:child_process';
import { createConnection } from 'node:net';
import { mkdirSync, openSync, closeSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const logs = resolve(root, '.forge/logs');
mkdirSync(logs, { recursive: true });
const processes = [];
const listening = port => new Promise(resolve => {
  const socket = createConnection({ host: '127.0.0.1', port });
  socket.setTimeout(500);
  socket.on('connect', () => { socket.destroy(); resolve(true); });
  socket.on('error', () => resolve(false));
  socket.on('timeout', () => { socket.destroy(); resolve(false); });
});
function start(name, executable, args) {
  const output = openSync(resolve(logs, `${name}.log`), 'a');
  const child = spawn(executable, args, { cwd: root, detached: true, windowsHide: true, stdio: ['ignore', output, output] });
  child.on('error', error => { console.error(`${name}: ${error.message}`); process.exitCode = 1; });
  child.unref(); closeSync(output);
  processes.push({ name, pid: child.pid });
}
async function wait(port) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if (await listening(port)) return;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`Port ${port} did not become ready; inspect ${logs}`);
}
function uv(args) {
  const result = spawnSync('uv', args, { cwd: root, stdio: 'inherit', windowsHide: true });
  if (result.error || result.status !== 0) throw result.error || new Error(`uv ${args.join(' ')} failed`);
}
try {
  if (await listening(8000)) throw new Error('Port 8000 is already occupied. Use the existing runtime or stop it before starting another worker.');
  uv(['sync', '--frozen']);
  if (!await listening(55432)) { start('postgres', process.execPath, ['scripts/postgres.mjs']); await wait(55432); }
  uv(['run', 'alembic', 'upgrade', 'head']);
  const python = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  start('api', python, ['-m', 'uvicorn', 'forgeagent.api:app', '--host', '127.0.0.1', '--port', '8000']);
  await wait(8000);
  const ready = await fetch('http://127.0.0.1:8000/health/ready');
  if (!ready.ok) throw new Error('API database readiness failed');
  start('worker', python, ['-m', 'forgeagent.worker']);
  let port = 5173;
  while (await listening(port)) port++;
  start('web', process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort']);
  await wait(port);
  console.log(`ForgeAgent: http://127.0.0.1:${port}/\nAPI: http://127.0.0.1:8000/docs\nLogs: ${logs}`);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  writeFileSync(resolve(root, '.forge/local-processes.json'), JSON.stringify({ processes }, null, 2));
}
