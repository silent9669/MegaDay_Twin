#!/usr/bin/env node
// Sinh bộ dữ liệu giả lập MegaDay Twin.
// Dùng: node generate.js [--size nho|demo|tai_nang] [--seed 123] [--out ./out/demo]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRng } from './src/rng.js';
import { fmtDateTime, parseDate } from './src/time.js';
import { buildCatalog } from './src/catalog.js';
import { simulate } from './src/simulate.js';
import { Exporter } from './src/export.js';

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const m = argv[i].match(/^--([^=]+)(?:=(.*))?$/);
    if (!m) continue;
    args[m[1]] = m[2] ?? argv[++i];
  }
  return args;
}

const here = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const config = JSON.parse(fs.readFileSync(path.join(here, 'config.json'), 'utf8'));
const size = args.size ?? 'demo';
if (!config.sizes[size]) {
  console.error(`Không có cỡ "${size}". Chọn một trong: ${Object.keys(config.sizes).join(', ')}`);
  process.exit(1);
}
const seed = Number(args.seed ?? config.seed);
const outDir = path.resolve(args.out ?? path.join(here, 'out', size));

// Chỉ xóa thư mục cũ nếu đó là output của script này.
if (fs.existsSync(outDir) && fs.readdirSync(outDir).length) {
  if (!fs.existsSync(path.join(outDir, 'ground_truth', 'manifest.json'))) {
    console.error(`Thư mục ${outDir} đã có dữ liệu khác. Chọn --out khác hoặc dọn thư mục trước.`);
    process.exit(1);
  }
  fs.rmSync(outDir, { recursive: true, force: true });
}

const timeline = {
  startMs: parseDate(config.timeline.start),
  cutoffMs: parseDate(config.timeline.historyCutoff),
  endMs: parseDate(config.timeline.end),
};

const started = Date.now();
const catalog = buildCatalog({ rng: createRng(seed), config, targetSkus: config.sizes[size].targetSkus, timeline });
const exporter = new Exporter({ outDir, timeline, config, rng: createRng(seed + 1), catalog, size, seed });
simulate({ catalog, timeline, config, rng: createRng(seed + 2), emit: exporter });
const { rowCounts } = exporter.finish();

const fmt = n => n.toLocaleString('vi-VN');
console.log(`\nĐã sinh dữ liệu cỡ "${size}" (seed ${seed}) trong ${((Date.now() - started) / 1000).toFixed(1)} giây`);
console.log(`→ ${outDir}`);
console.log(`  ${catalog.skus.length} SKU, ${catalog.campaigns.length} chiến dịch, lịch sử ${fmtDateTime(timeline.startMs)} → ${fmtDateTime(timeline.cutoffMs - 60_000)}, phát lại ngày 11.11\n`);
console.log('Số dòng:');
for (const [k, v] of Object.entries(rowCounts)) console.log(`  ${k.padEnd(18)} ${fmt(v)}`);

console.log('\nChiến dịch của nhân vật demo (lịch sử):');
console.log('  Mã     ROAS sàn  iROAS thật  Tên');
for (const c of catalog.campaigns.filter(c => c.skus.some(s => s.persona))) {
  const r = c.truth.roas == null ? '-' : c.truth.roas.toFixed(2);
  const i = c.truth.iroas == null ? '-' : c.truth.iroas.toFixed(2);
  console.log(`  ${c.id}  ${r.padStart(8)}  ${i.padStart(10)}  ${c.name}`);
}

const bySku = code => catalog.skus.find(s => s.sku === code);
const noi = bySku('NOICHIEN2L');
const aoM = bySku('AOGIUNHIET-M');
const binh = catalog.skus.filter(s => s.persona === 'binh_nuoc_viral');
const binhLive = binh.reduce((a, s) => a + s.truth.demandLive, 0);
const binhAvg = binh.reduce((a, s) => a + s.truth.demandLast7, 0) / 7;
console.log('\nKiểm nhanh kịch bản 11.11:');
console.log(`  Nồi chiên: tồn đầu ngày ${noi.truth.stockAtCutoff}, hết hàng lúc ${noi.truth.firstLiveStockout ?? 'không hết'}`);
console.log(`  Áo giữ nhiệt size M: hết hàng lúc ${aoM.truth.firstLiveStockout ?? 'không hết'}`);
console.log(`  Bình nước: nhu cầu 11.11 = ${binhLive} sp, trung bình 7 ngày trước = ${binhAvg.toFixed(0)} sp/ngày`);
console.log('\nĐáp án nằm trong ground_truth/ — không nạp vào app.\n');
