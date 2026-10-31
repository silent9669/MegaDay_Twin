#!/usr/bin/env node
// Phát lại ngày 11.11 theo từng giờ vào thư mục live_stream/, như dữ liệu đang về thật.
// Dùng: node replay.js [--size demo] [--interval 20] [--from 0]
//   --interval: số giây thật cho mỗi giờ mô phỏng (mặc định 20)
//   --from: bắt đầu từ giờ nào (0–23)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const m = process.argv[i].match(/^--([^=]+)(?:=(.*))?$/);
  if (m) args[m[1]] = m[2] ?? process.argv[++i];
}

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.resolve(args.dir ?? path.join(here, 'out', args.size ?? 'demo'));
const replayFile = path.join(dir, 'live_11_11', '_replay.json');
if (!fs.existsSync(replayFile)) {
  console.error(`Không thấy ${replayFile}. Chạy "node generate.js --size ${args.size ?? 'demo'}" trước.`);
  process.exit(1);
}
const replay = JSON.parse(fs.readFileSync(replayFile, 'utf8'));
const interval = Number(args.interval ?? 20);
const outDir = path.join(dir, 'live_stream');
fs.mkdirSync(outDir, { recursive: true });

// Ghi ra file tạm rồi đổi tên, để app không đọc phải file đang ghi dở.
function writeAtomic(file, content) {
  fs.writeFileSync(file + '.tmp', content);
  fs.renameSync(file + '.tmp', file);
}

const p2 = n => String(n).padStart(2, '0');
let hour = Number(args.from ?? 0);

function step() {
  const adsUntil = hour - replay.adsLagHours;
  for (const key of ['orders', 'ads', 'inventory']) {
    const maxHour = key === 'ads' ? adsUntil : hour;
    const lines = replay.rows[key].filter(([h]) => h <= maxHour).map(([, line]) => line);
    writeAtomic(path.join(outDir, replay.files[key]), '﻿' + replay.headers[key] + '\n' + lines.map(l => l + '\n').join(''));
  }
  writeAtomic(path.join(outDir, 'trang_thai.json'), JSON.stringify({
    ngay: replay.ngay,
    don_hang_va_ton_den_het: `${p2(hour)}:59`,
    ads_den_het: adsUntil >= 0 ? `${p2(adsUntil)}:59` : 'chưa có',
    cap_nhat_luc: new Date().toISOString(),
  }, null, 2));
  console.log(`[${new Date().toLocaleTimeString('vi-VN')}] Đã phát tới ${replay.ngay} ${p2(hour)}:59 (ads tới ${adsUntil >= 0 ? p2(adsUntil) + ':59' : 'chưa có'})`);
  hour++;
  if (hour < 24) setTimeout(step, interval * 1000);
  else console.log(`Đã phát hết ngày ${replay.ngay}. Dữ liệu ở ${outDir}`);
}

console.log(`Phát lại ${replay.ngay}: mỗi giờ mô phỏng = ${interval} giây. Ghi vào ${outDir}`);
step();
