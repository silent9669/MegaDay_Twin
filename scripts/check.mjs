#!/usr/bin/env node
// Run from any directory: node scripts/check.mjs [web|backend|mock-data ...]
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const tasks = {
  backend: ['backend', 'uv', ['run', '--locked', 'python', 'tasks.py', 'check']],
  web: ['web', 'npm', ['run', 'check']],
  'mock-data': ['.', 'node', ['scripts/check-mock-data.mjs']],
};
const selected = process.argv.slice(2);
if (selected.some((name) => !Object.hasOwn(tasks, name))) {
  console.error('Usage: node scripts/check.mjs [web|backend|mock-data ...]');
  process.exit(2);
}
for (const name of selected.length ? selected : Object.keys(tasks)) {
  const [cwd, command, args] = tasks[name];
  console.log(`\nChecking ${name}...`);
  const result = spawnSync(command, args, {
    cwd: `${root}/${cwd}`, stdio: 'inherit', shell: process.platform === 'win32',
    env: { ...process.env, CF_SEND_TELEMETRY: 'false' },
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status ?? 1);
}
