// Bộ sinh số ngẫu nhiên có seed (mulberry32): cùng seed thì cả team sinh ra cùng một bộ dữ liệu.
export function createRng(seed) {
  let state = seed >>> 0;

  function next() {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  const uniform = (a, b) => a + (b - a) * next();
  const int = (a, b) => Math.floor(uniform(a, b + 1));
  const chance = p => next() < p;
  const pick = arr => arr[Math.floor(next() * arr.length)];

  function normal() {
    let u = 0;
    while (u === 0) u = next();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  }

  // Marsaglia–Tsang
  function gamma(shape, scale) {
    if (shape < 1) return gamma(shape + 1, scale) * Math.pow(next() || 1e-12, 1 / shape);
    const d = shape - 1 / 3;
    const c = 1 / Math.sqrt(9 * d);
    for (;;) {
      let x, v;
      do {
        x = normal();
        v = 1 + c * x;
      } while (v <= 0);
      v = v * v * v;
      const u = next();
      if (u < 1 - 0.0331 * x ** 4) return d * v * scale;
      if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v * scale;
    }
  }

  function poisson(lambda) {
    if (lambda <= 0) return 0;
    if (lambda > 40) return Math.max(0, Math.round(lambda + Math.sqrt(lambda) * normal()));
    const limit = Math.exp(-lambda);
    let k = 0;
    let p = 1;
    do {
      k++;
      p *= next();
    } while (p > limit);
    return k - 1;
  }

  // Gamma–Poisson: dao động mạnh hơn Poisson, nhiều giờ bằng 0, giống doanh số thật.
  const negBinomial = (mean, dispersion) =>
    mean <= 0 ? 0 : poisson(gamma(dispersion, mean / dispersion));

  function shuffle(arr) {
    const out = [...arr];
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(next() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }

  function weightedKey(weights) {
    const entries = Object.entries(weights);
    const total = entries.reduce((sum, [, w]) => sum + w, 0);
    let r = next() * total;
    for (const [key, w] of entries) {
      r -= w;
      if (r <= 0) return key;
    }
    return entries[entries.length - 1][0];
  }

  return { next, uniform, int, chance, pick, normal, gamma, poisson, negBinomial, shuffle, weightedKey };
}
