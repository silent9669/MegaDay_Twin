import { DAY, HOUR, parseDate, startOfDay } from './time.js';

// Tỷ trọng đơn theo giờ trong ngày (tổng = 1): thấp về đêm, cao buổi trưa và 20–22h.
export const HOUR_PROFILE = [
  0.012, 0.007, 0.005, 0.004, 0.004, 0.006, 0.015, 0.03, 0.045, 0.055, 0.06, 0.065,
  0.065, 0.055, 0.05, 0.05, 0.05, 0.052, 0.055, 0.062, 0.07, 0.075, 0.068, 0.04,
];
const WEEKDAY = [1.08, 0.94, 0.96, 0.98, 0.98, 1.0, 1.08]; // CN → T7
const TET_2027 = parseDate('2027-02-06');
const GIFT_PEAKS = [parseDate('2026-10-19 12:00'), parseDate('2026-11-19 12:00')];
const WINTER_MID = parseDate('2026-10-25');

// Ngày đôi của sàn: 8.8, 9.9, 10.10, 11.11…
export function isDoubleDay(ms) {
  const d = new Date(ms);
  return d.getUTCMonth() + 1 === d.getUTCDate();
}

export function doubleDayHourMult(h) {
  if (h === 0) return 7;
  if (h === 1) return 4;
  if (h <= 8) return 2;
  if (h === 12) return 3.5;
  if (h === 20 || h === 21) return 3;
  return 2.3;
}

function eventMult(ms) {
  const day = startOfDay(ms);
  if (isDoubleDay(day)) return doubleDayHourMult(new Date(ms).getUTCHours());
  if (isDoubleDay(day + DAY)) return 0.85; // khách chờ sale
  if (isDoubleDay(day - DAY)) return 1.1;
  if (new Date(ms).getUTCDate() === 25) return 1.15; // ngày lương
  return 1;
}

function giftPeak(ms, peak) {
  const d = (ms - peak) / DAY;
  if (d <= 0) return 1 + 4 * Math.exp(-(d * d) / 32);
  if (d <= 1) return 5;
  return 0.45 + 0.55 * (1 - Math.exp(-(d - 1) / 15));
}

function seasonalMult(kind, ms) {
  switch (kind) {
    case 'qua_tang':
      return GIFT_PEAKS.reduce((m, peak) => m * giftPeak(ms, peak), 1);
    case 'tet':
      return 1 + 6 * Math.exp(-Math.max(0, (TET_2027 - ms) / DAY) / 25);
    case 'mua_dong':
      return 1 + 2.5 / (1 + Math.exp(-(ms - WINTER_MID) / DAY / 8));
    default:
      return 1;
  }
}

function trendMult(trend, ms) {
  if (!trend) return 1;
  const d = (ms - trend.peakMs) / DAY;
  const shape = d < 0 ? Math.exp(-(d * d) / (2 * trend.sigmaDays ** 2)) : Math.exp(-d / trend.tauDays);
  return 1 + trend.amp * shape;
}

function launchMult(launchMs, ms) {
  if (launchMs == null) return 1;
  if (ms < launchMs) return 0;
  return 1 - 0.8 * Math.exp(-(ms - launchMs) / (3 * DAY));
}

function orderQty(rng) {
  const r = rng.next();
  return r < 0.85 ? 1 : r < 0.97 ? 2 : 3;
}

const availableOf = s => Math.max(0, s.components ? Math.min(...s.components.map(c => c.stock)) : s.stock);

export function simulate({ catalog, timeline, config, rng, emit }) {
  const { skus, campaigns } = catalog;
  const { startMs, endMs } = timeline;
  const { ads, demand: dem, orders: ord } = config;
  const totalDays = Math.round((endMs - startMs) / DAY);
  const alpha = 0.02; // nửa đời ~34 giờ cho thứ hạng tự nhiên

  // Voucher theo sản phẩm: mọi biến thể giảm giá cùng ngày.
  const discounts = new Map();
  for (const s of skus) {
    if (!discounts.has(s.productId)) {
      const arr = new Float64Array(totalDays);
      for (let d = 0; d < totalDays; d++) {
        if (isDoubleDay(startMs + d * DAY)) arr[d] = rng.uniform(0.12, 0.25);
        else if (rng.chance(0.04)) arr[d] = rng.uniform(0.08, 0.15);
      }
      discounts.set(s.productId, arr);
    }
    s.discountByDay = discounts.get(s.productId);
    s.adstock = 0;
    s.spend = 0;
    s.emaSales = s.emaBase = Math.max(s.dailyBase / 24, 1e-3);
  }
  // Combo xử lý sau cùng vì lấy hàng từ tồn của sản phẩm thành phần.
  const ordered = [...skus.filter(s => !s.components), ...skus.filter(s => s.components)];

  function startDay(ms) {
    const mult = isDoubleDay(ms) ? ads.eventBudgetMultiplier : 1;
    for (const c of campaigns) c.remaining = c.dailyBudget * mult;

    const dayIdx = Math.round((ms - startMs) / DAY);
    for (const s of skus) {
      // Nhân vật demo: nhập bù lên mức cố định theo chu kỳ, để kịch bản 11.11 ổn định ở mọi cỡ dữ liệu.
      const policy = s.restockPolicy;
      if (policy && ms < policy.until && dayIdx % policy.everyDays === 0 && s.stock < policy.level) {
        const at = ms + 10 * HOUR;
        s.pending.push({ auto: false, createdMs: ms - 3 * DAY, plannedMs: at, arriveMs: at, qty: policy.level - s.stock });
      }
      if (!s.autoReorder || s.components || ms >= s.autoReorderUntil) continue;
      if (s.launchMs != null && ms < s.launchMs) continue;
      if (s.pending.some(p => p.auto)) continue;
      const rate = Math.max(s.dailyBase * 1.4, s.emaSales * 24);
      if (s.stock >= rate * s.leadDays * 1.3) continue;
      const plannedMs = ms + s.leadDays * DAY + 10 * HOUR;
      const delay = rng.chance(0.15) ? rng.int(2, 4) * DAY : 0;
      s.pending.push({
        auto: true,
        createdMs: ms + 9 * HOUR,
        plannedMs,
        arriveMs: plannedMs + delay + rng.int(0, 6) * HOUR,
        qty: Math.max(5, Math.round(rate * rng.uniform(25, 40))),
      });
    }
  }

  function applyInventoryEvents(s, ms) {
    for (const ev of s.stockEvents) {
      if (ev.done || ms < ev.ms) continue;
      ev.done = true;
      ev.before = s.stock;
      s.stock = ev.setTo;
      if (ev.clearAutoPending) s.pending = s.pending.filter(p => !p.auto);
      emit.onStockEvent(s, ev);
    }
    if (!s.pending.length) return;
    const arrived = s.pending.filter(p => ms >= p.arriveMs);
    if (!arrived.length) return;
    s.pending = s.pending.filter(p => ms < p.arriveMs);
    for (const p of arrived) {
      s.stock += p.qty;
      emit.onArrival(s, p);
    }
  }

  function campaignStatus(c, ms, h, doubleDay) {
    if (ms < c.startMs) return null;
    if (c.episodes.some(([a, b]) => ms >= a && ms < b)) return 'Tạm dừng';
    if (c.nightOff && !doubleDay && h < 6) return 'Tạm dừng';
    if (c.stockRule && c.mainSkus.some(s => s.stock < ads.pauseWhenStockBelow)) return 'Tạm dừng';
    return 'Đang chạy';
  }

  for (let ms = startMs; ms < endMs; ms += HOUR) {
    const date = new Date(ms);
    const h = date.getUTCHours();
    const dayIdx = Math.floor((ms - startMs) / DAY);
    const doubleDay = isDoubleDay(startOfDay(ms));
    const common = HOUR_PROFILE[h] * WEEKDAY[date.getUTCDay()] * eventMult(ms);

    if (h === 0) startDay(ms);
    for (const s of skus) applyInventoryEvents(s, ms);

    // 1. Nhu cầu nền, chưa có ads
    for (const s of ordered) {
      const disc = s.discountByDay[dayIdx];
      s.effPrice = Math.round((s.price * (1 - disc)) / 1000) * 1000;
      s.noRank = s.dailyBase * common * seasonalMult(s.kind, ms) * trendMult(s.trend, ms)
        * launchMult(s.launchMs, ms) * Math.pow(1 - disc, s.elasticity);
      const rank = s.emaBase > 1e-4
        ? Math.min(1.25, Math.max(0.85, 1 + dem.rankWeight * (s.emaSales / s.emaBase - 1)))
        : 1;
      s.lambdaBase = s.noRank * rank;
      s.spend = 0;
    }

    // 2. Quảng cáo theo chiến dịch; ngân sách chia cho SKU theo nhu cầu
    for (const c of campaigns) {
      const status = campaignStatus(c, ms, h, doubleDay);
      const budget = c.dailyBudget * (doubleDay ? ads.eventBudgetMultiplier : 1);
      let spend = 0;
      const base = c.skus.reduce((sum, s) => sum + s.lambdaBase, 0);
      if (status === 'Đang chạy' && base > 0 && c.remaining > 0) {
        const eventBoost = doubleDay ? doubleDayHourMult(h) / 2.4 : 1;
        const desired = budget * HOUR_PROFILE[h] * eventBoost * 1.3 * rng.uniform(0.8, 1.2);
        spend = Math.min(desired, c.remaining);
        c.remaining -= spend;
        for (const s of c.skus) s.spend = (spend * s.lambdaBase) / base;
      }
      let clicks = 0;
      let impressions = 0;
      if (spend > 0) {
        clicks = Math.max(1, Math.round((spend / c.cpc) * rng.uniform(0.9, 1.1)));
        impressions = Math.round((clicks / c.ctr) * rng.uniform(0.9, 1.1));
      }
      c.hour = {
        status: status === 'Đang chạy' && spend === 0 && c.remaining <= 0 ? 'Hết ngân sách' : status,
        budget, spend, clicks, impressions, attrOrders: 0, attrRevenue: 0, incRevenue: 0,
      };
    }

    // 3. Bán hàng: nhu cầu thật bị chặn bởi tồn kho
    for (const s of ordered) {
      s.adstock = s.spend + ads.adstockDecay * s.adstock;
      const uplift = s.adsA > 0 ? s.adsA * (1 - Math.exp(-s.adstock / s.k)) : 0;
      const lambda = s.lambdaBase * (1 + uplift);
      const demand = rng.negBinomial(lambda, dem.dispersion);
      const available = availableOf(s);
      const sold = Math.min(demand, available);
      // Sàn quy cả đơn tự nhiên cho ads nên ROAS báo cáo cao hơn hiệu quả thật.
      const attrShare = s.spend > 0 ? Math.min(0.9, (ads.attributionInflation * uplift) / (1 + uplift)) : 0;

      let left = sold;
      let net = 0;
      while (left > 0) {
        const qty = Math.min(left, orderQty(rng));
        left -= qty;
        const orderMs = ms + rng.int(0, 59) * 60_000;
        const cancelled = rng.chance(ord.cancelRate);
        const returned = !cancelled && rng.chance(ord.returnRate);
        const attributed = rng.chance(attrShare);
        if (!cancelled) net += qty;
        if (attributed && s.campaign) {
          s.campaign.hour.attrOrders += 1;
          s.campaign.hour.attrRevenue += qty * s.effPrice;
        }
        emit.onOrder({
          ms: orderMs, sku: s, qty, effPrice: s.effPrice, cancelled, returned, attributed,
          cancelMs: cancelled ? orderMs + rng.uniform(0.5, 24) * HOUR : null,
          returnMs: returned ? orderMs + rng.uniform(4, 8) * DAY : null,
        });
      }

      if (s.components) for (const c of s.components) c.stock -= net;
      else s.stock -= net;

      const fill = demand > 0 ? sold / demand : 1;
      const incUnits = s.lambdaBase * uplift * fill;
      if (s.campaign) s.campaign.hour.incRevenue += incUnits * s.effPrice;

      emit.onSkuHour({
        ms, sku: s, lambdaBase: s.lambdaBase, lambda, demand, sold, net,
        stockout: demand > available, spend: s.spend, adstock: s.adstock, uplift,
        incUnits, effPrice: s.effPrice, stockEnd: availableOf(s),
      });

      s.emaSales = (1 - alpha) * s.emaSales + alpha * net;
      s.emaBase = (1 - alpha) * s.emaBase + alpha * s.noRank;
    }

    for (const c of campaigns) if (c.hour.status) emit.onCampaignHour(c, ms, c.hour);
    emit.onHourEnd(ms);
    if (h === 23) emit.onDayEnd(startOfDay(ms));
  }
}
