#!/usr/bin/env node
// Chạy cùng lúc backend (FastAPI, cổng 8000) và web (Vite, cổng 5173) cho dev.
// Dùng: node scripts/dev.mjs [--fixtures] [--db <đường dẫn DuckDB>] [--no-migrate]
//                           [--api-port 8000] [--web-port 5173]
//   --fixtures   chỉ chạy web với dữ liệu mẫu (không cần backend)
//   --db         DuckDB cho backend (mặc định DUCKDB_PATH trong backend/.env)
//   --no-migrate bỏ bước migrate trước khi bật API
// Ctrl+C tắt cả hai. Một bên thoát thì bên kia cũng được tắt.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const fixtures = args.includes('--fixtures');
const noMigrate = args.includes('--no-migrate');
const dbIdx = args.indexOf('--db');
const db = dbIdx >= 0 ? args[dbIdx + 1] : undefined;
const opt = (name, def) => (args.indexOf(name) >= 0 ? args[args.indexOf(name) + 1] : def);
const apiPort = opt('--api-port', '8000');
const webPort = opt('--web-port', '5173');
const isWin = process.platform === 'win32';

const COLORS = { api: '\x1b[36m', web: '\x1b[35m', dev: '\x1b[33m' };
const RESET = '\x1b[0m';
const log = (tag, line) => process.stdout.write(`${COLORS[tag] ?? ''}[${tag}]${RESET} ${line}\n`);

const children = [];
let stopping = false;

function run(tag, cmd, cmdArgs, cwd, env = {}) {
  const child = spawn(cmd, cmdArgs, {
    cwd,
    env: { ...process.env, ...env },
    shell: isWin, // npm/uv là .cmd trên Windows
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  for (const stream of [child.stdout, child.stderr]) {
    let buf = '';
    stream.on('data', (chunk) => {
      buf += chunk.toString();
      const lines = buf.split(/\r?\n/);
      buf = lines.pop() ?? '';
      for (const line of lines) if (line.trim()) log(tag, line);
    });
  }
  return child;
}

function stopAll(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const c of children) {
    if (c.exitCode !== null) continue;
    if (isWin) spawn('taskkill', ['/pid', String(c.pid), '/T', '/F'], { stdio: 'ignore' });
    else c.kill('SIGTERM');
  }
  setTimeout(() => process.exit(code), 500);
}

function waitExit(child) {
  return new Promise((resolve) => child.on('exit', (code) => resolve(code ?? 1)));
}

async function main() {
  const backend = path.join(root, 'backend');
  const web = path.join(root, 'web');
  const apiEnv = db ? { DUCKDB_PATH: db } : {};

  if (!fixtures) {
    if (!noMigrate) {
      log('dev', 'Migrate database…');
      const code = await waitExit(run('api', 'uv', ['run', 'python', '-m', 'app.cli', 'migrate'], backend, apiEnv));
      if (code !== 0) {
        log('dev', `Migrate lỗi (mã ${code}). Kiểm tra uv và Python 3.11 (xem README).`);
        process.exit(code);
      }
    }
    children.push(
      run('api', 'uv', ['run', 'python', '-m', 'uvicorn', 'app.main:app', '--reload', '--port', apiPort], backend, apiEnv),
    );
  }
  const webEnv = { VITE_PROXY_TARGET: `http://localhost:${apiPort}`, ...(fixtures ? { VITE_USE_FIXTURES: 'true' } : {}) };
  children.push(run('web', 'npm', ['run', 'dev', '--', '--port', webPort, '--strictPort'], web, webEnv));

  log('dev', fixtures
    ? `Web (dữ liệu mẫu): http://localhost:${webPort}`
    : `API: http://localhost:${apiPort}/docs · Web: http://localhost:${webPort} · Ctrl+C để tắt`);

  for (const c of children) {
    c.on('exit', (code) => {
      if (stopping) return;
      log('dev', `Một tiến trình đã thoát (mã ${code ?? '?'}), tắt phần còn lại.`);
      stopAll(code ?? 1);
    });
  }
}

process.on('SIGINT', () => stopAll(0));
process.on('SIGTERM', () => stopAll(0));
main();
