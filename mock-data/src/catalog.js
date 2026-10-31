import { DAY, HOUR, parseDate } from './time.js';

const COLORS = [['Xanh', 'XANH'], ['Hồng', 'HONG'], ['Đen', 'DEN'], ['Trắng', 'TRANG']];
const DIMS = [['Nhỏ', 'NHO'], ['Vừa', 'VUA'], ['Lớn', 'LON']];
const CAPS = [['500ml', '500'], ['750ml', '750'], ['1 lít', '1L']];
const PACKS = [['Gói 1', 'G1'], ['Gói 3', 'G3'], ['Gói 5', 'G5']];
const DESIGNS = [['Mẫu A', 'A'], ['Mẫu B', 'B'], ['Mẫu C', 'C']];
const LENS = [['5m', '5M'], ['10m', '10M']];
const NONE = [];

// [tên, mã, giá thấp (nghìn đ), giá cao (nghìn đ), biến thể, loại mùa vụ]
const TEMPLATES = {
  on_dinh: [
    ['Khăn lau bếp microfiber', 'KHANLAU', 25, 60, PACKS],
    ['Hộp đựng thực phẩm thủy tinh', 'HOPTT', 89, 189, CAPS],
    ['Móc dán tường chịu lực', 'MOCDAN', 19, 45, PACKS],
    ['Túi zip bảo quản thực phẩm', 'TUIZIP', 29, 59, PACKS],
    ['Thớt gỗ tràm', 'THOTGO', 79, 159, DIMS],
    ['Kệ gia vị xoay', 'KEGIAVI', 119, 249, COLORS],
    ['Chảo chống dính đáy từ', 'CHAO', 189, 389, DIMS],
    ['Hộp cơm giữ nhiệt', 'HOPCOM', 149, 299, COLORS],
    ['Bộ đũa inox 10 đôi', 'DUAINOX', 49, 99, NONE],
    ['Bàn chải vệ sinh nhà tắm', 'BANCHAI', 29, 69, COLORS],
    ['Giá phơi đồ gấp gọn', 'GIAPHOI', 159, 329, NONE],
    ['Thảm chùi chân thấm nước', 'THAM', 49, 129, COLORS],
    ['Ly thủy tinh chịu nhiệt', 'LYTT', 39, 89, CAPS],
    ['Đèn ngủ cảm ứng', 'DENNGU', 79, 169, COLORS],
    ['Rổ nhựa đa năng', 'RONHUA', 25, 55, COLORS],
  ],
  mua_vu: [
    ['Hộp quà hoa sáp', 'HOPQUA', 149, 299, COLORS, 'qua_tang'],
    ['Nến thơm tinh dầu', 'NENTHOM', 89, 199, DESIGNS, 'qua_tang'],
    ['Gấu bông ôm', 'GAUBONG', 129, 349, DIMS, 'qua_tang'],
    ['Bình hoa gốm', 'BINHHOA', 159, 359, COLORS, 'qua_tang'],
    ['Bao lì xì in nổi', 'LIXI', 19, 49, PACKS, 'tet'],
    ['Câu đối đỏ treo tường', 'CAUDOI', 59, 149, DESIGNS, 'tet'],
    ['Đèn lồng đỏ', 'DENLONG', 49, 129, DIMS, 'tet'],
    ['Chăn lông cừu', 'CHANLONG', 289, 559, COLORS, 'mua_dong'],
    ['Tất giữ nhiệt', 'TATGN', 29, 69, PACKS, 'mua_dong'],
  ],
  trend: [
    ['Cốc tự khuấy', 'COCKHUAY', 99, 199, COLORS],
    ['Đèn LED cuộn trang trí phòng', 'LEDCUON', 59, 149, LENS],
    ['Móc khóa hoạt hình', 'MOCKHOA', 19, 39, DESIGNS],
    ['Quạt cầm tay mini', 'QUATMINI', 79, 169, COLORS],
    ['Gương LED để bàn', 'GUONGLED', 149, 299, COLORS],
  ],
  moi: [
    ['Bình lọc nước mini', 'BINHLOC', 199, 399, NONE],
    ['Máy đánh trứng cầm tay', 'DANHTRUNG', 149, 289, COLORS],
    ['Hộp đựng mỹ phẩm xoay', 'HOPMYPHAM', 129, 259, COLORS],
  ],
  ban_thua: [
    ['Bộ dao Nhật 5 món', 'BODAO', 690, 1290, NONE],
    ['Nồi gang đúc', 'NOIGANG', 890, 1590, DIMS],
    ['Máy xay hạt cà phê', 'MAYXAY', 459, 890, NONE],
    ['Đèn bàn gỗ decor', 'DENBAN', 289, 559, NONE],
  ],
};

const PERSONA_CAMPAIGNS = {
  binh_nuoc: { name: '[Tìm kiếm] Bình nước giữ nhiệt', type: 'Tìm kiếm' },
  bep: { name: '[Tìm kiếm] Nồi chiên mini + giấy lót', type: 'Tìm kiếm' },
  den_tet: { name: '[Khám phá] Đèn LED Tết', type: 'Khám phá' },
  coc: { name: '[Khám phá] Cốc in hình meme', type: 'Khám phá', budgetFactor: 2.5 },
  ao: { name: '[Tìm kiếm] Áo giữ nhiệt nam', type: 'Tìm kiếm' },
  may_hut: { name: '[Tìm kiếm] Máy hút bụi mini', type: 'Tìm kiếm' },
};

export const PERSONA_STORIES = {
  binh_nuoc_viral: 'Bán đều nhiều tháng, bất ngờ viral từ trưa 11.11 (đỉnh khoảng 18:00). Nhu cầu có thể vượt tồn.',
  den_tet_ton_nhieu: 'Hàng Tết, tồn rất nhiều so với nhịp bán hiện tại. Bài toán: bán dần bằng ads hay giữ hàng tới gần Tết.',
  noi_chien_sap_can: 'Bán chạy, ads hiệu quả thật cao. Đầu 11.11 tồn còn ít, hàng về chiều 12/11. Bài toán: tiếp tục ads hay chậm lại chờ hàng.',
  giay_lot: 'Dùng chung chiến dịch và ngân sách với nồi chiên.',
  combo_noi_chien: 'Combo trừ tồn cả nồi chiên lẫn giấy lót, nên nồi chiên cạn nhanh hơn dự kiến.',
  coc_trend_nguoi: 'Trend đạt đỉnh giữa tháng 10, đang nguội, tồn nhiều. Ads tốn nhiều nhưng hiệu quả thật thấp.',
  ao_het_size_m: 'Mùa đông kéo nhu cầu lên. Size M hết hàng giữa ngày 11.11 trong khi ads vẫn chạy: số bán tụt nhưng không phải hết trend.',
  may_hut_bui_moi: 'Mới ra mắt 04/11, chỉ có 7 ngày dữ liệu. App nên hiện nhãn "kịch bản" thay vì dự báo đã kiểm.',
};

function personaDefs(T) {
  return [
    {
      persona: 'binh_nuoc_viral', campaign: 'binh_nuoc',
      name: 'Bình nước giữ nhiệt 500ml', code: 'BINHNUOC500', group: 'on_dinh',
      price: 189000, cost: 92000, elasticity: -1.8, adsA: 0.5,
      trend: { peakMs: T('2026-11-11 18:00'), amp: 6, sigmaDays: 0.25, tauDays: 1.5 },
      variants: [
        { name: 'Xanh', code: 'XANH', base: 6, stock: 400 },
        { name: 'Hồng', code: 'HONG', base: 4, stock: 300 },
        { name: 'Đen', code: 'DEN', base: 5, stock: 350 },
      ],
    },
    {
      persona: 'den_tet_ton_nhieu', campaign: 'den_tet',
      name: 'Đèn LED trang trí Tết', code: 'DENLEDTET', group: 'mua_vu', kind: 'tet',
      elasticity: -2.2, adsA: 0.35, autoReorder: false,
      variants: [
        { name: 'Dây 5m', code: '5M', base: 3, stock: 1500, price: 129000, cost: 58000 },
        { name: 'Dây 10m', code: '10M', base: 2, stock: 900, price: 219000, cost: 99000 },
      ],
    },
    {
      persona: 'noi_chien_sap_can', campaign: 'bep',
      name: 'Nồi chiên không dầu mini 2L', code: 'NOICHIEN2L', group: 'on_dinh',
      price: 690000, cost: 455000, elasticity: -2.0, adsA: 0.8,
      variants: [{
        name: '', code: '', base: 20, stock: 380, autoReorder: false,
        restockPolicy: { level: 380, everyDays: 7, until: T('2026-11-04') },
        manualRestocks: [{ createdMs: T('2026-11-05 09:00'), plannedMs: T('2026-11-12 10:00'), arriveMs: T('2026-11-12 16:00'), qty: 400 }],
      }],
    },
    {
      persona: 'giay_lot', campaign: 'bep',
      name: 'Giấy lót nồi chiên 50 tờ', code: 'GIAYLOT50', group: 'on_dinh',
      price: 45000, cost: 18000, elasticity: -1.6, adsA: 0.3,
      variants: [{ name: '', code: '', base: 10, stock: 600 }],
    },
    {
      persona: 'combo_noi_chien', campaign: 'bep',
      name: 'Combo nồi chiên mini + giấy lót', code: 'COMBO-NOICHIEN', group: 'on_dinh',
      price: 719000, elasticity: -2.0, adsA: 0.6, autoReorder: false,
      componentsOf: ['NOICHIEN2L', 'GIAYLOT50'],
      variants: [{ name: '', code: '', base: 4, stock: 0 }],
    },
    {
      persona: 'coc_trend_nguoi', campaign: 'coc',
      name: 'Cốc in hình meme', code: 'COCMEME', group: 'trend',
      price: 89000, cost: 32000, elasticity: -2.4, adsA: 0.15, autoReorder: false,
      trend: { peakMs: T('2026-10-15'), amp: 8, sigmaDays: 7, tauDays: 10 },
      variants: [
        { name: 'Mẫu A', code: 'A', base: 1.5, stock: 1200 },
        { name: 'Mẫu B', code: 'B', base: 1.2, stock: 1000 },
        { name: 'Mẫu C', code: 'C', base: 1.0, stock: 900 },
      ],
    },
    {
      persona: 'ao_het_size_m', campaign: 'ao',
      name: 'Áo giữ nhiệt nam', code: 'AOGIUNHIET', group: 'mua_vu', kind: 'mua_dong',
      price: 159000, cost: 70000, elasticity: -1.9, adsA: 0.45,
      variants: [
        { name: 'Size S', code: 'S', base: 3, stock: 150 },
        {
          name: 'Size M', code: 'M', base: 5, stock: 200, autoReorder: false,
          restockPolicy: { level: 260, everyDays: 7, until: T('2026-11-04') },
          stockEvents: [{ ms: T('2026-11-11 10:00'), setTo: 12, clearAutoPending: true, note: 'Loại {chenh} áo size M in nhầm size khỏi kho, còn 12' }],
          manualRestocks: [{ createdMs: T('2026-11-09 15:00'), plannedMs: T('2026-11-14 10:00'), arriveMs: T('2026-11-15 11:00'), qty: 250 }],
        },
        { name: 'Size L', code: 'L', base: 4, stock: 180 },
      ],
    },
    {
      persona: 'may_hut_bui_moi', campaign: 'may_hut',
      name: 'Máy hút bụi cầm tay mini', code: 'MAYHUTMINI', group: 'moi',
      price: 459000, cost: 250000, elasticity: -2.0, adsA: 0.6, launchMs: T('2026-11-04 08:00'),
      variants: [{ name: '', code: '', base: 6, stock: 300 }],
    },
  ];
}

function addProduct(ctx, p) {
  const productNo = ctx.products.length + 1;
  const product = { id: `P${String(productNo).padStart(4, '0')}`, name: p.name, skus: [] };
  ctx.products.push(product);
  const internalBase = `GD${String(productNo).padStart(4, '0')}`;

  for (const v of p.variants) {
    const sku = {
      sku: v.code ? `${p.code}-${v.code}` : p.code,
      internalCode: v.code ? `${internalBase}-${v.code.slice(0, 2)}` : internalBase,
      productId: product.id,
      productName: p.name,
      variantName: v.name || '',
      group: p.group,
      kind: p.kind ?? null,
      persona: p.persona ?? null,
      price: v.price ?? p.price,
      cost: v.cost ?? p.cost ?? null,
      elasticity: p.elasticity,
      dailyBase: v.base,
      adsA: v.adsA ?? p.adsA ?? 0,
      trend: p.trend ?? null,
      launchMs: p.launchMs ?? null,
      stock: Math.round(v.stock),
      autoReorder: v.autoReorder ?? p.autoReorder ?? true,
      autoReorderUntil: v.autoReorderUntil ?? p.autoReorderUntil ?? Infinity,
      stockEvents: (v.stockEvents ?? []).map(e => ({ ...e })),
      pending: (v.manualRestocks ?? []).map(r => ({ ...r, auto: false })),
      restockPolicy: v.restockPolicy ?? null,
      leadDays: ctx.rng.int(3, 10),
      componentsOf: p.componentsOf ?? null,
      components: null,
      campaignKey: p.campaign ?? null,
      campaign: null,
      k: 1,
    };
    product.skus.push(sku);
    ctx.skus.push(sku);
  }
  return product;
}

function randomProduct(ctx, config, remaining) {
  const { rng, timeline } = ctx;
  const group = rng.weightedKey(config.groupMix);
  const list = TEMPLATES[group];
  const n = (ctx.counters[group] = (ctx.counters[group] ?? 0) + 1);
  const [name, code, pmin, pmax, variantList, kind] = list[(n - 1) % list.length];
  const rep = Math.floor((n - 1) / list.length);

  const price = rng.int(pmin, pmax) * 1000;
  const vcount = variantList.length ? rng.int(1, Math.min(4, variantList.length)) : 1;
  const chosen = (variantList.length ? variantList.slice(0, vcount) : [['', '']]).slice(0, remaining);

  const totalBase = {
    on_dinh: () => Math.exp(rng.uniform(Math.log(2), Math.log(16))),
    mua_vu: () => rng.uniform(1, 5),
    trend: () => rng.uniform(0.5, 2.5),
    moi: () => rng.uniform(2, 7),
    ban_thua: () => rng.uniform(0.05, 0.5),
  }[group]();
  const weights = chosen.map((_, j) => 1 / (j + 1) ** 0.7);
  const weightSum = weights.reduce((a, b) => a + b, 0);

  const trend = group === 'trend'
    ? { peakMs: timeline.cutoffMs + rng.uniform(-60, 5) * DAY, amp: rng.uniform(3, 10), sigmaDays: rng.uniform(3, 10), tauDays: rng.uniform(6, 20) }
    : null;
  const launchMs = group === 'moi' ? timeline.cutoffMs - Math.round(rng.uniform(5, 20) * 24) * HOUR : null;
  const autoReorder = !(group === 'trend' || kind === 'tet');
  const hasAds = rng.chance(group === 'ban_thua' ? 0.3 : config.ads.shareProductsWithAds);

  const variants = chosen.map(([vname, vcode], j) => {
    const base = (totalBase * weights[j]) / weightSum;
    const stock = {
      on_dinh: () => base * rng.uniform(30, 60) * 1.5,
      moi: () => base * rng.uniform(20, 40),
      mua_vu: () => (kind === 'tet' ? base * rng.uniform(150, 250) : base * rng.uniform(40, 70) * 1.5),
      trend: () => base * trend.amp * rng.uniform(12, 25),
      ban_thua: () => rng.int(5, 25),
    }[group]();
    return { name: vname, code: vcode, base, stock: Math.max(5, stock) };
  });

  addProduct(ctx, {
    name: rep ? `${name} (mẫu ${rep + 1})` : name,
    code: rep ? `${code}${rep + 1}` : code,
    group, kind, price,
    cost: Math.round((price * rng.uniform(0.42, 0.62)) / 500) * 500,
    elasticity: -rng.uniform(1.2, 2.6),
    adsA: hasAds ? rng.uniform(0.15, 0.85) : 0,
    trend, launchMs, autoReorder, variants,
  });
}

function makeCampaign(ctx, config, id, def, skus) {
  const { rng, timeline } = ctx;
  const base = skus.reduce((sum, s) => sum + s.dailyBase, 0);
  const revenue = skus.reduce((sum, s) => sum + s.dailyBase * s.price, 0);
  const dailyBudget = Math.max(50_000, Math.round((revenue * config.ads.budgetShareOfRevenue * rng.uniform(0.7, 1.3) * (def.budgetFactor ?? 1)) / 1000) * 1000);
  const launches = skus.map(s => s.launchMs).filter(ms => ms != null);
  const startMs = launches.length === skus.length ? Math.min(...launches) : timeline.startMs;

  const episodes = [];
  const pauseCount = rng.int(1, 3);
  for (let i = 0; i < pauseCount; i++) {
    const from = Math.max(startMs, timeline.startMs) + 7 * DAY;
    const to = timeline.cutoffMs - 5 * DAY;
    if (to <= from) break;
    const begin = from + Math.floor(rng.uniform(0, (to - from) / HOUR)) * HOUR;
    episodes.push([begin, begin + rng.int(6, 48) * HOUR]);
  }

  const campaign = {
    id, name: def.name, type: def.type, skus, dailyBudget, startMs, episodes,
    nightOff: def.persona ? false : rng.chance(0.3),
    stockRule: def.persona ? false : rng.chance(0.6),
    mainSkus: [...skus].sort((a, b) => b.dailyBase - a.dailyBase).slice(0, 2),
    cpc: rng.uniform(800, 3500),
    ctr: rng.uniform(0.012, 0.035),
    remaining: 0,
    hour: null,
  };
  for (const s of skus) {
    s.campaign = campaign;
    // k: mức chi tích lũy để ads đạt khoảng 63% hiệu quả tối đa.
    s.k = Math.max(1, ((dailyBudget * (s.dailyBase / base)) / 24) * 2.5);
  }
  return campaign;
}

export function buildCatalog({ rng, config, targetSkus, timeline }) {
  const ctx = { rng, timeline, skus: [], products: [], counters: {} };

  for (const def of personaDefs(parseDate)) addProduct(ctx, def);
  for (const s of ctx.skus) {
    if (s.componentsOf) s.components = s.componentsOf.map(code => ctx.skus.find(x => x.sku === code));
  }
  while (ctx.skus.length < targetSkus) randomProduct(ctx, config, targetSkus - ctx.skus.length);

  const campaigns = [];
  let seq = 0;
  const nextId = () => `CD${String(++seq).padStart(3, '0')}`;

  for (const [key, def] of Object.entries(PERSONA_CAMPAIGNS)) {
    const skus = ctx.skus.filter(s => s.campaignKey === key);
    campaigns.push(makeCampaign(ctx, config, nextId(), { ...def, persona: true }, skus));
  }

  const byProduct = new Map();
  for (const s of ctx.skus) {
    if (s.persona || s.adsA <= 0) continue;
    if (!byProduct.has(s.productId)) byProduct.set(s.productId, []);
    byProduct.get(s.productId).push(s);
  }
  const products = rng.shuffle([...byProduct.values()]);
  while (products.length) {
    const chunk = products.splice(0, rng.int(1, 4));
    const skus = chunk.flat();
    const type = rng.pick(['Tự động', 'Tìm kiếm', 'Khám phá']);
    const name = `[${type}] ${skus[0].productName}${chunk.length > 1 ? ` + ${chunk.length - 1} SP khác` : ''}`;
    campaigns.push(makeCampaign(ctx, config, nextId(), { name, type }, skus));
  }

  return { skus: ctx.skus, products: ctx.products, campaigns };
}
