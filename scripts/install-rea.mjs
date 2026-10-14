import { mkdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = new URL('../', import.meta.url);
const destination = new URL('.forge/rea/', root);
const npmCli = process.env.npm_execpath;
if (!npmCli) throw new Error('Run this installer using npm run rea:install.');
await mkdir(destination, { recursive: true });
for (const name of ['package.json', 'package-lock.json']) {
  await copyFile(new URL(`integrations/rea/${name}`, root), new URL(name, destination));
}
const result = spawnSync(process.execPath, [npmCli, 'ci', '--prefix', fileURLToPath(destination),
  '--ignore-scripts', '--no-audit', '--no-fund'], { stdio: 'inherit', windowsHide: true });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status || 1);
console.log('Pinned REA 6.4.0 installed. Configure FORGE_REA_RUNTIME_DIR and approved target roots separately.');
