// Tổng quan ca against real-API-shaped responses (M3-api §5), with `api.get` stubbed:
// which endpoints are called, grouping, cursor paging and the 09 §4 states.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { api, ApiError } from '../api/client';
import type { SkuListItem } from '../api/types';
import { routes } from '../routes';

const META = { data_as_of: '2026-11-11T00:00:00+07:00', computed_at: '2026-10-30T15:10:47+07:00', compute_state: null, confidence: null, sources: [], cache_hit: null };

function sku(over: Partial<SkuListItem>): SkuListItem {
  return {
    sku: 'NOICHIEN2L',
    name: 'Nồi chiên không dầu mini 2L',
    variant: '',
    product_id: 'NOICHIEN2L',
    is_bundle: false,
    demand_group: 'on_dinh',
    stock_available: 83,
    hours_of_cover: { p10: 12, p50: 17, p90: 35, horizon_hours: 72 },
    sold_today_units: 36,
    sold_3h_units: 10,
    deviation: null,
    campaign: { id: 'CD002', status: 'Đang chạy', status_code: 'running' },
    recommendation: {
      recommendation_id: 'rec-A',
      status: 'undetermined',
      best: { kind: 'schedule_hours', pct: null, windows_local: [[0, 2], [12, 14]] },
      confidence: 'reference',
      scenario_id: 'scn-A',
      created_at: '2026-10-30T14:52:52+07:00',
    },
    open_alerts: 0,
    priority_score: 21.67,
    confidence: 'reference',
    forecast_id: 'fc-1',
    compute_state: 'fresh',
    computed_at: '2026-10-30T14:47:02+07:00',
    ...over,
  };
}

const ao = (size: string, prio: number) =>
  sku({
    sku: `AOGIUNHIET-${size}`,
    name: 'Áo giữ nhiệt nam',
    variant: `Size ${size}`,
    product_id: 'AOGIUNHIET',
    priority_score: prio,
    campaign: { id: 'CD005', status: 'Đang chạy', status_code: 'running' },
    recommendation: { ...sku({}).recommendation!, recommendation_id: 'rec-B', best: { kind: 'reduce_budget', pct: 0.5, windows_local: null } },
  });

const stockedOut = sku({ sku: 'CAUDOI-A', name: 'Câu đối đỏ', variant: 'Mẫu A', product_id: 'CAUDOI', stock_available: 0, priority_score: 50, recommendation: null, hours_of_cover: { p10: 0, p50: 0, p90: 0, horizon_hours: 72 } });

type Handler = (path: string) => unknown;

function stub(handler: Handler) {
  const calls: string[] = [];
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    calls.push(path);
    const res = handler(path);
    if (res instanceof Error) throw res;
    return res as never;
  });
  return calls;
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const common: Handler = (path) => {
  if (path === '/me') return { data: { id: 'u-ops-1', role: 'operator', role_label: 'Vận hành', can_run_scenarios: true, can_edit_goals: false }, meta: META };
  if (path === '/freshness')
    return {
      data: {
        now: '2026-11-11T00:00:00+07:00',
        clock: 'data',
        data_as_of: '2026-11-11T00:00:00+07:00',
        sources: [
          { source: 'orders', label: 'Đơn hàng', as_of: '2026-11-11T00:00:00+07:00', status: 'fresh', lag_minutes: 0, threshold_minutes: 30 },
          { source: 'inventory', label: 'Tồn kho', as_of: '2026-11-10T20:00:00+07:00', status: 'stale', lag_minutes: 240, threshold_minutes: 60 },
        ],
      },
      meta: META,
    };
  if (path.startsWith('/recommendations')) return { data: [], meta: META, next_cursor: null };
  if (path.startsWith('/alerts')) return new ApiError(404, 'NOT_FOUND', 'Không có');
  return undefined;
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Tổng quan ca · real API shapes', () => {
  it('calls /skus, /freshness, /me; groups variants; shows stale banner and role', async () => {
    const calls = stub((path) => {
      const c = common(path);
      if (c !== undefined) return c;
      if (path.startsWith('/skus?sort=priority&limit=200')) return { data: [stockedOut, sku({}), ao('M', 20), ao('S', 18)], meta: META, next_cursor: null };
      if (path.startsWith('/skus?')) return { data: [stockedOut, sku({})], meta: META, next_cursor: 'eyJvIjoyfQ' };
      throw new Error(`unexpected ${path}`);
    });
    renderAt('/');

    const attention = await screen.findByRole('region', { name: /Cần xem ngay \(2\)/ });
    const cards = within(attention).getAllByRole('listitem');
    expect(cards[0]).toHaveTextContent('Nồi chiên không dầu mini 2L');
    expect(cards[0]).toHaveTextContent('Còn 83 sp · hết khoảng 17:00');
    expect(within(cards[0] as HTMLElement).getByRole('link')).toHaveAttribute('href', '/sku/NOICHIEN2L/compare');
    expect(cards[1]).toHaveTextContent('Áo giữ nhiệt nam(2 phân loại)');
    expect(within(cards[1] as HTMLElement).getByRole('link')).toHaveAttribute('href', '/sku/AOGIUNHIET-M/compare');
    expect(within(attention).queryByText(/Câu đối/)).not.toBeInTheDocument();

    expect(await screen.findByText('Vai trò: Vận hành (u-ops-1)')).toBeInTheDocument();
    expect(screen.getByText(/Dữ liệu Tồn cũ hơn ngưỡng/)).toBeInTheDocument();

    const table = await screen.findByRole('table');
    expect(within(table).getByText('CAUDOI-A')).toBeInTheDocument();
    expect(calls).toEqual(expect.arrayContaining(['/me', '/freshness', '/skus?sort=priority&limit=200', '/skus?sort=priority&limit=50']));
  });

  it('"Tải thêm" follows next_cursor', async () => {
    const calls = stub((path) => {
      const c = common(path);
      if (c !== undefined) return c;
      if (path === '/skus?sort=priority&limit=200') return { data: [sku({})], meta: META, next_cursor: null };
      if (path === '/skus?sort=priority&limit=50') return { data: [sku({})], meta: META, next_cursor: 'CUR1' };
      if (path === '/skus?sort=priority&limit=50&cursor=CUR1') return { data: [stockedOut], meta: META, next_cursor: null };
      throw new Error(`unexpected ${path}`);
    });
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: 'Tải thêm' }));
    expect(await screen.findByText('CAUDOI-A')).toBeInTheDocument();
    expect(screen.getByText('2 mã')).toBeInTheDocument();
    expect(calls).toContain('/skus?sort=priority&limit=50&cursor=CUR1');
  });

  it('meta.total → "Toàn bộ SKU (N)" and "Đã tải n/N mã"', async () => {
    stub((path) => {
      const c = common(path);
      if (c !== undefined) return c;
      if (path === '/skus?sort=priority&limit=200') return { data: [sku({})], meta: { ...META, total: 2 }, next_cursor: null };
      if (path === '/skus?sort=priority&limit=50') return { data: [sku({})], meta: { ...META, total: 2 }, next_cursor: 'CUR1' };
      throw new Error(`unexpected ${path}`);
    });
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'Toàn bộ SKU (2)' })).toBeInTheDocument();
    expect(await screen.findByText('Đã tải 1/2 mã')).toBeInTheDocument();
  });

  it('empty: no SKU → import call to action', async () => {
    stub((path) => common(path) ?? { data: [], meta: META, next_cursor: null });
    renderAt('/');
    expect(await screen.findByText('Chưa có dữ liệu SKU')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: 'Nhập dữ liệu' });
    expect(links.some((a) => a.className.includes('btn-primary') && a.getAttribute('href') === '/import')).toBe(true);
  });

  it('nothing to review: says when it was last computed', async () => {
    stub((path) => common(path) ?? { data: [stockedOut], meta: META, next_cursor: null });
    renderAt('/');
    expect(await screen.findByText('Không có SKU nào cần xem ngay. Lần tính gần nhất lúc 14:47.')).toBeInTheDocument();
  });

  it('error: API failure shows what failed and a retry button', async () => {
    stub((path) => common(path) ?? new ApiError(401, 'UNAUTHENTICATED', 'Thiếu thông tin người dùng (X-User).'));
    renderAt('/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Thiếu thông tin người dùng (X-User).');
    expect(alert).toHaveTextContent('Mã lỗi: UNAUTHENTICATED');
    expect(within(alert).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});
