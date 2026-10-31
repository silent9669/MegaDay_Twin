/** Display-only subset of backend/app/core/money.py. Never rewrites source data.
 * Unrecognised or unsafe amounts stay pending backend/user review.
 */
export function previewVnd(value: string): number | null {
  let text = value.replace(/₫/g, "d").normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "").replace(/đ/gi, "d")
    .toLowerCase().replace(/\s+/g, "");
  if (text.length > 80) return null;
  text = text.replace(/(?:vnd|dong|d)$/, "").replace(/^d/, "");
  let multiplier = 1n;
  if (text.endsWith("k")) { multiplier = 1000n; text = text.slice(0, -1); }
  else if (text.endsWith("tr")) { multiplier = 1000000n; text = text.slice(0, -2); }
  const negative = text.startsWith("-");
  if (negative) text = text.slice(1);
  if (/^\d{1,3}(\.\d{3})+$/.test(text)) text = text.replace(/\./g, "");
  else if (multiplier === 1n && /^\d{1,3}(,\d{3})+$/.test(text)) text = text.replace(/,/g, "");
  else text = text.replace(/,/g, ".");
  if (!/^\d+(\.\d+)?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const scale = 10n ** BigInt(fraction.length);
  const numerator = BigInt(whole! + fraction) * multiplier;
  // Decimal ROUND_HALF_UP, including negative values; avoid float rounding drift.
  const rounded = (numerator * 2n + scale) / (2n * scale);
  if (rounded > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(negative ? -rounded : rounded);
}
