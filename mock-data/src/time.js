// Mọi mốc thời gian là giờ Việt Nam (GMT+7), lưu dưới dạng epoch "giả UTC"
// để kết quả không phụ thuộc múi giờ của máy chạy script.
export const HOUR = 3_600_000;
export const DAY = 24 * HOUR;

const p2 = n => String(n).padStart(2, '0');

export function parseDate(text) {
  const [date, time = '00:00'] = text.trim().split(' ');
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  return Date.UTC(y, m - 1, d, hh, mm || 0);
}

export const startOfDay = ms => ms - (ms % DAY);
export const hourOf = ms => new Date(ms).getUTCHours();

export function fmtDate(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`;
}

export function fmtDateTime(ms) {
  const d = new Date(ms);
  return `${fmtDate(ms)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
}
