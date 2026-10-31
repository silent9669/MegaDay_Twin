#!/usr/bin/env node
// Một lệnh chuẩn bị demo pitch MegaDay Twin (M5). Chạy ở gốc repo.
//
//   node scripts/demo.mjs [--db <file .duckdb>] [--out <thư mục mới>] [--light]
//                         [--skip-goal] [--skip-warm] [--no-web]
//                         [--api-port 8000] [--web-port 5173]
//
// Các bước:
//   1. DB demo ĐẦY ĐỦ (phương án A, như lúc chấm điểm): app.demo_import --replay-hour 0 --models
//      (lịch sử + 11.11 qua đúng thư mục phát lại tới 00:59, tức 01:00; backtest + fit + ước lượng
//      iROAS; ~3–5 phút, máy bận 5–10 phút; in tiến độ) vào thư mục MỚI backend/data/pitch-<thời gian> (không bao
//      giờ ghi đè), hoặc dùng DB có sẵn với --db. Số pitch và 3 bản ghi AI đo trên DB này.
//      --light: bản nhẹ (--estimate-ads, không backtest/fit, ~1–3 phút) khi cần nhanh: số what-if
//      và bản ghi AI KHÔNG khớp pitch (docs/quyet-dinh/M5-chuan-bi-demo.md "Phương án A").
//   2. Bật backend với AI_ENABLED=true, AI_MODE=replay, DEMO_MODE=true, LIVE_ENABLED=true,
//      LIVE_DIR=<thư mục DB>/live_stream (riêng, không lẫn live_stream cũ của replay.js).
//      Biến môi trường chỉ truyền cho tiến trình con; backend/.env không bị sửa.
//   3. Mục tiêu profit CD002 (lead) nếu chưa có; làm nóng màn So sánh CD002 (POST /scenarios/run).
//   4. Bật web (Vite) trỏ proxy vào backend.
//   5. In tóm tắt: URL, trạng thái từng bước, lệnh nhảy giờ, 3 câu AI phải gõ nguyên văn.
//
// Điều khiển phát lại khi đang demo (cửa sổ terminal khác, cùng --api-port):
//   node scripts/demo.mjs jump <H>              dữ liệu tới H:59 (vd 10 → 10:59, 13 → 13:59)
//   node scripts/demo.mjs home                  về lại 01:00 (reset + jump 0)
//   node scripts/demo.mjs replay [từ giờ] [giây] phát lại tự động mỗi giờ (mặc định 9, 20 s)
//   node scripts/demo.mjs stop | status
//
// Ctrl+C tắt cả backend và web. Không bao giờ in key.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { kill, log, run, waitExit } from './lib/proc.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.join(root, 'backend');
const web = path.join(root, 'web');
const shop = path.join(root, 'mock-data', 'out', 'demo');
const CLOCK = '2026-11-11T01:00:00+07:00';
const QUESTIONS = [
  ['explain', 'Vì sao nồi chiên chỉ chạy khung giờ?', '01:00'],
  ['whatif', 'Nếu sếp giao chỉ tiêu 97 cái hôm nay thì sao?', '01:00 (cần mục tiêu profit CD002)'],
  ['investigate', 'Vì sao áo giữ nhiệt bán chậm?', '14:00 (sau jump 13)'],
];

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const opt = (name, def) => (argv.indexOf(name) >= 0 ? argv[argv.indexOf(name) + 1] : def);
const apiPort = opt('--api-port', '8000');
const webPort = opt('--web-port', '5173');
const api = `http://127.0.0.1:${apiPort}/api/v1`;
const LEAD = { 'X-User': 'lead-1', 'X-Role': 'lead' };
const OPERATOR = { 'X-User': 'op-demo', 'X-Role': 'operator' };

async function call(method, p, headers, body, timeoutMs = 600_000) {
  const res = await fetch(api + p, {
    method,
    headers: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await res.text();
  let json;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = { raw: text };
  }
  if (!res.ok) {
    const msg = json?.error?.message ?? text.slice(0, 200);
    throw new Error(`${method} ${p} → HTTP ${res.status}: ${msg}`);
  }
  return json;
}

const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }) : '?');
const tr = (vnd) => `${(vnd / 1e6).toFixed(2).replace('.', ',')} tr`;

// ------------------------------------------------------------------ live subcommands
async function liveCommand(cmd) {
  const control = (body) => call('POST', '/live/control', LEAD, { wait: true, ...body });
  let st;
  if (cmd === 'jump') {
    const hour = Number(argv[1]);
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) throw new Error('Cần giờ 0–23, vd: node scripts/demo.mjs jump 13');
    log('demo', `Nhảy tới ${String(hour).padStart(2, '0')}:59 và chờ tính lại + cảnh báo…`);
    st = (await control({ command: 'jump', hour })).data;
  } else if (cmd === 'home') {
    log('demo', 'Về 01:00: reset rồi jump 0…');
    await control({ command: 'reset' });
    st = (await control({ command: 'jump', hour: 0 })).data;
  } else if (cmd === 'replay') {
    const from = Number(argv[1] ?? 9);
    const interval = Number(argv[2] ?? 20);
    st = (await control({ command: 'start', from_hour: from, interval_seconds: interval })).data;
  } else if (cmd === 'stop') {
    st = (await control({ command: 'stop' })).data;
  } else {
    st = (await call('GET', '/live/status', LEAD)).data;
  }
  log('demo', `Dữ liệu tới ${st.orders_until ?? '—'} (ads tới ${st.ads_until ?? '—'}), data_as_of ${hhmm(st.data_as_of)}, phát lại ${st.replay_running ? 'đang chạy' : 'dừng'}`);
  if (cmd !== 'status') {
    const alerts = (await call('GET', '/alerts?status=open,acknowledged,snoozed&limit=20', LEAD)).data;
    for (const a of alerts.slice(0, 8)) log('demo', `  cảnh báo [${a.severity}] ${a.title}`);
  }
}

// ------------------------------------------------------------------ prepare + serve
function portBusy(port) {
  return new Promise((resolve) => {
    const s = net.connect({ port: Number(port), host: '127.0.0.1' });
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => resolve(false));
  });
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function resolveDb(p) {
  for (const base of [process.cwd(), backend]) {
    const abs = path.resolve(base, p);
    if (fs.existsSync(abs)) return abs;
  }
  return null;
}

async function waitHealth(child, timeoutMs = 180_000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (child.exitCode !== null) throw new Error(`Backend thoát sớm (mã ${child.exitCode}).`);
    try {
      await call('GET', '/health', OPERATOR, undefined, 3000);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  throw new Error('Backend không trả /health sau 180 giây.');
}

const steps = [];
const LABEL = { ok: 'ĐẠT', skip: 'BỎ QUA', warn: 'CẢNH BÁO', error: 'LỖI' };
const mark = (name, status, detail = '') => {
  steps.push({ name, status, detail });
  log('prep', `${LABEL[status] ?? 'LỖI'} · ${name}${detail ? ` · ${detail}` : ''}`);
};

const children = [];
let stopping = false;
function stopAll(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const c of children) kill(c);
  setTimeout(() => process.exit(code), 500);
}

async function prepare() {
  // 1. DB
  let db;
  if (opt('--db')) {
    db = resolveDb(opt('--db'));
    if (!db) throw new Error(`Không thấy DB ${opt('--db')}`);
    mark('DB demo', 'skip', `dùng DB có sẵn ${path.relative(root, db)}`);
  } else {
    const out = path.resolve(backend, opt('--out', path.join('data', `pitch-${stamp()}`)));
    if (fs.existsSync(out)) throw new Error(`${out} đã có; chọn --out mới (không ghi đè).`);
    const light = flag('--light');
    if (flag('--models')) log('prep', '--models không còn cần: mặc định đã dựng DB đầy đủ (backtest + fit).');
    if (light) log('prep', 'CẢNH BÁO: --light = BẢN NHẸ (không backtest/fit), SỐ KHÁC PITCH; bản ghi AI có thể ra "ngoại tuyến".');
    log('prep', `Dựng DB demo ${light ? 'NHẸ' : 'đầy đủ'} tới 01:00 ngày 11.11 vào ${path.relative(root, out)} (${light ? 'ước lượng iROAS, ~1–3 phút' : 'backtest + fit + ước lượng iROAS, ~3–5 phút; máy bận thì lâu hơn'})…`);
    const t0 = Date.now();
    const args = ['run', 'python', '-m', 'app.demo_import', '--shop', shop, '--output', out, '--replay-hour', '0', light ? '--estimate-ads' : '--models'];
    const child = run('prep', 'uv', args, backend, {}, { filter: (l) => !l.startsWith('{') || /ingest_done/.test(l) });
    const tick = setInterval(() => log('prep', `… đang dựng DB, ${Math.round((Date.now() - t0) / 1000)} s`), 30_000);
    const code = await waitExit(child).finally(() => clearInterval(tick));
    if (code !== 0) throw new Error(`demo_import lỗi (mã ${code}).`);
    db = path.join(out, 'demo.duckdb');
    mark('DB demo', light ? 'warn' : 'ok', `${path.relative(root, db)} (${Math.round((Date.now() - t0) / 1000)} s${light ? ', BẢN NHẸ: số khác pitch' : ', đầy đủ: backtest + fit + iROAS'})`);
  }

  // 2. backend
  if (await portBusy(apiPort)) throw new Error(`Cổng ${apiPort} đang bận. Dùng --api-port khác.`);
  if (!flag('--no-web') && (await portBusy(webPort))) throw new Error(`Cổng ${webPort} đang bận. Dùng --web-port khác.`);
  const liveDir = path.join(path.dirname(db), 'live_stream');
  const apiEnv = {
    APP_ENV: 'demo',
    DUCKDB_PATH: db,
    DATA_DIR: shop,
    LIVE_DIR: liveDir,
    LIVE_ENABLED: 'true',
    DEMO_MODE: 'true',
    AI_ENABLED: 'true',
    AI_MODE: 'replay',
    CORS_ORIGINS: `http://localhost:${webPort},http://127.0.0.1:${webPort}`,
  };
  const apiChild = run('api', 'uv', ['run', 'python', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', apiPort], backend, apiEnv, {
    filter: (l) => !/"event": "(http_request|request)"/.test(l),
  });
  children.push(apiChild);
  await waitHealth(apiChild);
  const ai = (await call('GET', '/ai/status', OPERATOR)).data;
  mark('Backend', 'ok', `cổng ${apiPort}, AI ${ai.enabled ? 'bật' : 'tắt'} mode=${ai.mode}, DEMO_MODE, live → ${path.relative(root, liveDir)}`);

  // 3. goal + warm-up
  if (flag('--skip-goal')) mark('Mục tiêu CD002', 'skip');
  else {
    const goals = (await call('GET', '/goals?scope_id=CD002', LEAD)).data;
    if (goals.some((g) => g.kind === 'profit')) mark('Mục tiêu CD002', 'ok', 'đã có mục tiêu profit');
    else {
      await call('POST', '/goals', LEAD, { scope: 'campaign', scope_id: 'CD002', kind: 'profit', deadline: '2026-11-12T00:00:00+07:00', reason: 'Lãi trong ngày 11.11' });
      mark('Mục tiêu CD002', 'ok', 'lead-1 đặt profit, hạn 24:00 ngày 11.11');
    }
  }
  if (flag('--skip-warm')) mark('Làm nóng So sánh CD002', 'skip');
  else {
    const t0 = Date.now();
    try {
      const runOut = (await call('POST', '/scenarios/run', OPERATOR, { campaign_id: 'CD002' })).data;
      const rec = runOut.recommendation;
      const by = Object.fromEntries(runOut.results.map((r) => [r.key, r]));
      const best = runOut.results.find((r) => JSON.stringify(r.action) === JSON.stringify(rec.best));
      const gain = best && by.continue ? best.objective.mean - by.continue.objective.mean : null;
      mark('Làm nóng So sánh CD002', best?.key === 'schedule_hours:0-2,12-14' ? 'ok' : 'error',
        `${best?.key ?? '?'}${gain !== null ? `, hơn chạy tiếp ${tr(gain)}` : ''} (${Math.round((Date.now() - t0) / 1000)} s)`);
    } catch (e) {
      mark('Làm nóng So sánh CD002', 'error', e.message);
    }
  }
  const fr = (await call('GET', '/freshness', OPERATOR)).data;
  const at0100 = new Date(fr.data_as_of).getTime() === new Date(CLOCK).getTime();
  mark('Đồng hồ dữ liệu', at0100 ? 'ok' : 'error', `${hhmm(fr.data_as_of)}${at0100 ? '' : ' (không phải 01:00: chạy "node scripts/demo.mjs home")'}`);

  // 4. web
  if (flag('--no-web')) mark('Web', 'skip');
  else {
    children.push(run('web', 'npm', ['run', 'dev', '--', '--port', webPort, '--strictPort'], web, { VITE_PROXY_TARGET: `http://127.0.0.1:${apiPort}` }));
    mark('Web', 'ok', `http://localhost:${webPort}`);
  }
  for (const c of children) {
    c.on('exit', (code) => {
      if (stopping) return;
      log('demo', `Một tiến trình đã thoát (mã ${code ?? '?'}), tắt phần còn lại.`);
      stopAll(code ?? 1);
    });
  }
  summary(db);
}

function summary(db) {
  const p = apiPort === '8000' ? '' : ` --api-port ${apiPort}`;
  const lines = [
    '',
    '================ DEMO SẴN SÀNG ================',
    `Web:  http://localhost:${webPort}   (So sánh nồi chiên: http://localhost:${webPort}/campaign/CD002/compare)`,
    `API:  http://localhost:${apiPort}/docs`,
    `DB:   ${path.relative(root, db)}   (bật lại không dựng: node scripts/demo.mjs --db ${path.relative(root, db)}${p})`,
    '',
    'Các bước:',
    ...steps.map((s) => `  ${(LABEL[s.status] ?? 'LỖI').padEnd(8)} ${s.name}${s.detail ? ` · ${s.detail}` : ''}`),
    ...(steps.some((s) => s.status === 'warn') ? ['', '  !! BẢN NHẸ (--light): số what-if và bản ghi AI KHÁC PITCH. Không dùng để pitch.'] : []),
    '',
    'Nhảy giờ (terminal khác):',
    `  node scripts/demo.mjs jump 10${p}    dữ liệu tới 10:59 → cảnh báo bình nước (nói "khoảng 11 giờ")`,
    `  node scripts/demo.mjs jump 13${p}    dữ liệu tới 13:59 → áo giữ nhiệt size M hết hàng, CD005 vẫn chạy`,
    `  node scripts/demo.mjs home${p}       về lại 01:00 (cảnh nồi chiên)`,
    `  node scripts/demo.mjs replay 9 20${p} phát lại tự động từ 9 giờ, 20 giây mỗi giờ; dừng: stop`,
    '',
    'AI (gõ đúng nguyên văn, đúng luồng, đúng mốc):',
    ...QUESTIONS.map(([flow, q, at]) => `  [${flow}] ${q}   ← mốc ${at}`),
    '',
    'Diễn tập tự động (2 lượt, có reset): cd backend; uv run python -m eval.rehearsal --base-url http://localhost:' + apiPort,
    'Ctrl+C để tắt backend + web.',
    '===============================================',
  ];
  for (const l of lines) log('demo', l);
}

const sub = argv[0] && !argv[0].startsWith('--') ? argv[0] : null;
process.on('SIGINT', () => stopAll(0));
process.on('SIGTERM', () => stopAll(0));
if (sub) {
  if (!['jump', 'home', 'replay', 'stop', 'status'].includes(sub)) {
    log('demo', `Lệnh lạ: ${sub}. Dùng jump <H> | home | replay [từ] [giây] | stop | status`);
    process.exit(2);
  }
  liveCommand(sub).then(() => process.exit(0), (e) => { log('demo', `LỖI: ${e.message}`); process.exit(1); });
} else {
  prepare().catch((e) => {
    log('demo', `LỖI: ${e.message}`);
    summary_safe();
    stopAll(1);
  });
}

function summary_safe() {
  for (const s of steps) log('demo', `  ${s.status} ${s.name} ${s.detail}`);
}
