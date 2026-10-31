// Tiến trình con dùng chung cho các script Node (cùng cách với scripts/dev.mjs):
// spawn có shell trên Windows (npm/uv là .cmd), log từng dòng có nhãn màu, tắt cả cây
// tiến trình bằng taskkill /T trên Windows.
import { spawn } from 'node:child_process';

export const isWin = process.platform === 'win32';

const COLORS = { api: '\x1b[36m', web: '\x1b[35m', dev: '\x1b[33m', demo: '\x1b[33m', prep: '\x1b[32m' };
const RESET = '\x1b[0m';

export const log = (tag, line) => process.stdout.write(`${COLORS[tag] ?? ''}[${tag}]${RESET} ${line}\n`);

/** Spawn `cmd args` in `cwd`; each output line is logged as `[tag] line` (unless `quiet`). */
export function run(tag, cmd, cmdArgs, cwd, env = {}, { quiet = false, filter } = {}) {
  const child = spawn(cmd, cmdArgs, {
    cwd,
    env: { ...process.env, ...env },
    shell: isWin,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  for (const stream of [child.stdout, child.stderr]) {
    let buf = '';
    stream.on('data', (chunk) => {
      buf += chunk.toString();
      const lines = buf.split(/\r?\n/);
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim() || quiet) continue;
        if (filter && !filter(line)) continue;
        log(tag, line);
      }
    });
  }
  return child;
}

export function waitExit(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) resolve(child.exitCode);
    else child.on('exit', (code) => resolve(code ?? 1));
  });
}

export function kill(child) {
  if (!child || child.exitCode !== null) return;
  if (isWin) spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  else child.kill('SIGTERM');
}
