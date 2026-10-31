// Serve the production build locally with isolated test credentials and local storage.
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes, pbkdf2Sync } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
const root = fileURLToPath(new URL('../../', import.meta.url));
const dir = resolve(root, '.cloudflare/studio-qa');
const main = resolve(root, '.cloudflare/output/v0/workers/default/bundle/index.js');
if (!existsSync(main)) throw Error('Run npm run build:cloudflare first.');
mkdirSync(dir, { recursive: true });
if (!existsSync(resolve(dir, '.dev.vars'))) {
  const salt = randomBytes(16).toString('base64url');
  const hash = pbkdf2Sync(process.env.STUDIO_QA_PASSWORD || 'studio-local-test', salt, 100000, 32, 'sha256').toString('base64url');
  writeFileSync(resolve(dir, '.dev.vars'), `ADMIN_PASSWORD_HASH=pbkdf2:${salt}:${hash}\nSESSION_SECRET=${randomBytes(32).toString('hex')}\n`, { mode: 0o600 });
}
const config = resolve(dir, 'wrangler.json');
writeFileSync(config, JSON.stringify({ name: 'megadaytwin-studio-qa', main, compatibility_date: '2026-10-30', compatibility_flags: ['nodejs_compat'],
  assets: { directory: resolve(root, '.cloudflare/output/v0/workers/default/assets'), binding: 'ASSETS', not_found_handling: 'single-page-application', run_worker_first: true },
  durable_objects: { bindings: [{ name: 'WORKSPACE', class_name: 'AccountWorkspace' }] },
  migrations: [{ tag: 'v1', new_sqlite_classes: ['AccountWorkspace'] }],
  d1_databases: [{ binding: 'DB', database_name: 'studio-qa', database_id: '00000000-0000-0000-0000-000000000001' }],
}));
const child = spawn(process.platform === 'win32' ? 'npx.cmd' : 'npx', ['wrangler', 'dev', '--config', config, '--local', '--ip', '127.0.0.1', '--port', '5187'], { cwd: root, stdio: 'inherit' });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', code => { process.exitCode = code ?? 0; });
