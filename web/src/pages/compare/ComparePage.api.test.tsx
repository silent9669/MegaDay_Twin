// So sánh lựa chọn against real-API-shaped responses (M3-api §5), with `api.get` /
// `api.post` stubbed. Covers the endpoint sequence, no zone_bar, infeasible actions,
// slider re-runs (300 → 1000 samples, echoed request) and the "no goal" flow.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { api, ApiError } from '../../api/client';
import type { CampaignDetail, Recommendation, ScenarioResult, ScenarioRun, SimRequest } from '../../api/types';
import { config } from '../../lib/config';
import { ComparePage,CompareIndexPage } from './ComparePage';
import { ShiftOverviewPage } from '../ShiftOverviewPage';
import { HQSelection } from '../../hq/selection';
import { NeedsRecommendation } from './CompareSetup';
const routes=[{path:'/',element:<ShiftOverviewPage/>},{path:'/compare',element:<CompareIndexPage/>},{path:'/campaign/:id/compare',element:<ComparePage/>},{path:'/sku/:sku/compare',element:<ComparePage/>}];

const META = {
  data_as_of: '2026-11-11T00:00:00+07:00',
  computed_at: '2026-10-30T14:52:52+07:00',
  compute_state: 'fresh' as const,
  confidence: 'reference' as const,
  sources: [],
  cache_hit: false,
};
const d = (mean: number) => ({ mean, p10: mean * 1.1, p50: mean, p90: mean * 0.9 });

function result(over: Partial<ScenarioResult> & Pick<ScenarioResult, 'action' | 'key' | 'label'>): ScenarioResult {
  return {
    feasible: true,
    violations: [],
    violation_details: [],
    objective: d(1_000_000),
    contribution: d(1_400_000),
    spend: d(1_000_000),
    leftover_units: d(200),
    leftover_value: d(2_000_000),
    units_sold: d(100),
    lost_units: null,
    stockout_hour_p50: '2026-11-11T14:00:00+07:00',
    per_sku: [],
    goal_eligible: true,
    ...over,
  };
}

const REC: Recommendation = {
  recommendation_id: 'rec-REAL',
  campaign_id: 'CD002',
  status: 'recommend',
  best: { kind: 'schedule_hours', pct: null, windows_local: [[0, 2], [12, 14]] },
  runner_up: { kind: 'reduce_budget', pct: 0.5, windows_local: null },
  best_label: 'Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00',
  runner_up_label: 'Giảm ngân sách 50%',
  p_best_beats_runner_up: 1,
  expected_gain_vnd: 1_516_953,
  zone_bar: null,
  flip_conditions: [],
  confidence: 'reference',
  reasons: [
    'Ở giá 11.11 (531.000đ) mỗi sp NOICHIEN2L lãi 9.280đ; ads chỉ có lời khi iROAS ≥ 57.2',
    'Giữ hàng lợi hơn bán: mỗi sp còn lại đáng 56.725đ, bán chỉ lãi 12.585đ',
  ],
  reason_sources: [
    {
      id: 'm-NOICHIEN2L',
      kind: 'contribution',
      label: 'Giá bán, giá vốn, phí sàn của NOICHIEN2L',
      tool: 'get_sku_snapshot',
      href: '/api/v1/skus/NOICHIEN2L',
      data_as_of: '2026-11-11T00:00:00+07:00',
    },
    null,
  ],
  inputs_ref: {
    forecast_ids: ['fc-1'],
    estimate_id: 'iroas-1',
    param_version: { leftover_value_per_unit: 0 },
    goal_id: 'goal-1',
    data_as_of: '2026-11-11T00:00:00+07:00',
    panel_version: 'ib-1',
    iroas_source: 'estimate',
  },
  scenario_id: 'scn-REAL',
  created_at: '2026-10-30T14:52:52+07:00',
  superseded_by: null,
  active: true,
};

const REQUEST = {
  campaign_id: 'CD002',
  start: '2026-11-11T00:00:00+07:00',
  horizon_hours: 24,
  goal_id: 'goal-1',
  actions: null,
  param_overrides: {},
  samples: 1000,
  seed: 20261111,
  session_id: null,
};

const RUN: ScenarioRun = {
  scenario_id: 'scn-REAL',
  request: REQUEST,
  results: [
    result({ key: 'continue', label: 'Chạy tiếp', action: { kind: 'continue', pct: null, windows_local: null }, objective: d(-1_614_751) }),
    result({
      key: 'pause',
      label: 'Tạm dừng',
      action: { kind: 'pause', pct: null, windows_local: null },
      feasible: false,
      violations: ['campaign_must_run'],
      objective: d(4_528_580),
    }),
    result({ key: 'reduce_budget:0.5', label: 'Giảm ngân sách 50%', action: { kind: 'reduce_budget', pct: 0.5, windows_local: null } }),
    result({
      key: 'schedule_hours:0-2,12-14',
      label: 'Chỉ chạy theo khung giờ',
      action: { kind: 'schedule_hours', pct: null, windows_local: [[0, 2], [12, 14]] },
      objective: d(2_722_332),
    }),
  ],
  recommendation: REC,
  cache_hit: false,
  blocked: false,
};

const CAMPAIGN: CampaignDetail = {
  campaign_id: 'CD002',
  name: '[Tìm kiếm] Nồi chiên mini + giấy lót',
  type: 'Tìm kiếm',
  daily_budget_vnd: 1_864_000,
  sku_level_control: false,
  skus: ['GIAYLOT50', 'NOICHIEN2L'],
  status: 'Đang chạy',
  status_code: 'running',
  status_at: null,
  spend_today_vnd: null,
  budget_used_pct: null,
  iroas: null,
  recommendation: null,
  started_at: null,
  sku_detail: [
    { sku: 'NOICHIEN2L', name: 'Nồi chiên không dầu mini 2L', variant: '', is_bundle: false, list_price_vnd: 690_000, price_effective_vnd: 690_000, stock_available: 83, unit_cost_vnd: 455_000 },
  ],
  price_max_vnd: 690_000,
  commitments: [{ kind: 'campaign_must_run', windows_local: [[0, 2]], text: 'Không tắt chiến dịch trong khung flash sale (đã cam kết với sàn)' }],
  goal: { goal_id: 'goal-1', kind: 'profit', label: 'Profit through 00:00 12/11', deadline: '2026-11-12T00:00:00+07:00', target_units: null, confirmed_by: 'u-lead-1', version: 1 },
};

const ME = (lead: boolean) => ({
  data: { id: lead ? 'u-lead-1' : 'u-ops-1', role: lead ? 'lead' : 'operator', role_label: lead ? 'Lead' : 'Operator', can_run_scenarios: true, can_edit_goals: lead },
  meta: META,
});

function stub(opts: { campaign?: CampaignDetail; recs?: Recommendation[]; lead?: boolean; post?: (path: string, body: unknown) => unknown }) {
  const gets: string[] = [];
  const posts: { path: string; body: unknown }[] = [];
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    gets.push(path);
    if (path === '/me') return ME(Boolean(opts.lead)) as never;
    if (path === '/freshness') return { data: { now: META.data_as_of, clock: 'data', data_as_of: META.data_as_of, sources: [] }, meta: META } as never;
    if (path.startsWith('/alerts')) throw new ApiError(404, 'NOT_FOUND', 'x');
    if (path.startsWith('/campaigns/')) return { data: opts.campaign ?? CAMPAIGN, meta: META } as never;
    if (path.startsWith('/recommendations?')) return { data: opts.recs ?? [REC], meta: META, next_cursor: null } as never;
    if (path === '/recommendations/rec-REAL') return { data: REC, meta: META } as never;
    if (path === '/scenarios/scn-REAL') return { data: RUN, meta: META } as never;
    throw new ApiError(404, 'NOT_FOUND', `unexpected ${path}`);
  });
  vi.spyOn(api, 'post').mockImplementation(async (path: string, body?: unknown) => {
    posts.push({ path, body });
    const res = opts.post?.(path, body);
    if (res instanceof Error) throw res;
    if (res !== undefined) return res as never;
    return { data: { ...RUN, scenario_id: `scn-${posts.length}` }, meta: { ...META, cache_hit: false } } as never;
  });
  return { gets, posts };
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

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

// config.useFixtures is read once at import; flip the exported object for these tests.
const mutableConfig = config as { useFixtures: boolean };
beforeAll(() => {
  mutableConfig.useFixtures = false;
});
afterAll(() => {
  mutableConfig.useFixtures = true;
});

describe('Compare · real API', () => {
  it('shows campaign loading errors instead of an empty recommendation state', async () => {
    stub({ recs: [] });
    const get = api.get;
    const original = vi.mocked(get).getMockImplementation()!;
    vi.mocked(get).mockImplementation((path, ...args) => path.startsWith('/campaigns/')
      ? Promise.reject(new ApiError(503, 'BACKEND_UNAVAILABLE', 'Campaign temporarily unavailable'))
      : original(path, ...args));
    renderAt('/campaign/CD002/compare');
    expect(await screen.findByText('Campaign temporarily unavailable')).toBeInTheDocument();
  });

  it('HQ directs missing goals through preview/apply without writing an active goal', async () => {
    const { posts } = stub({ lead: true });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([{ path: '/', element:
      <HQSelection.Provider value={{ product: null, open: vi.fn(), tab: vi.fn(), log: vi.fn() }}>
        <NeedsRecommendation campaign={{ ...CAMPAIGN, goal: null }} dataAsOf={META.data_as_of} />
      </HQSelection.Provider> }]);
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByRole('link', { name: 'Review a goal and preview before applying' }))
      .toHaveAttribute('href', '/city-demo?district=hq&hq=goals&campaign=CD002');
    expect(screen.queryByRole('form', { name: 'Set goal' })).not.toBeInTheDocument();
    expect(posts).toHaveLength(0);
  });
  it('loads campaign, active recommendation and scenario; explains the missing zone bar', async () => {
    const { gets } = stub({});
    renderAt('/campaign/CD002/compare');
    expect(await screen.findByText('Run 0–2h, 12–14h. About 1.5 million VND better than reduce budget 50%.')).toBeInTheDocument();
    expect(screen.getByText(/Holding stock is worth more than selling/)).toBeInTheDocument();
    expect(screen.getByText('Pausing is financially better but blocked by a flash-sale commitment.')).toBeInTheDocument();
    expect(screen.getByText('Goal: Profit through 00:00 12/11')).toBeInTheDocument();
    expect(screen.getByText(/Campaign: \[Tìm kiếm\] Nồi chiên mini \+ giấy lót/)).toBeInTheDocument();

    const cards = screen.getByRole('list', { name: 'Five actions' });
    const pause = within(cards).getAllByRole('listitem').find((li) => li.textContent?.startsWith('Pause ads'));
    expect(pause).toHaveTextContent('Not feasible');
    expect(pause).toHaveTextContent('Without the constraint: 4.5 M');

    expect(gets).toEqual(
      expect.arrayContaining(['/campaigns/CD002', '/recommendations?campaign_id=CD002&active=true', '/recommendations/rec-REAL', '/scenarios/scn-REAL']),
    );
  });

  it('reason_sources become citation chips that link to the app route', async () => {
    stub({});
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Reasons & assumptions' }));
    await userEvent.click(screen.getByRole('button', { name: /531,000 VND/ }));
    const dialog = screen.getByRole('dialog', { name: 'Source: Price, cost and platform fees for NOICHIEN2L' });
    expect(dialog).toHaveTextContent('get_sku_snapshot');
    expect(within(dialog).getByRole('link')).toHaveAttribute('href', '/sku/NOICHIEN2L');
  });

  it('slider: POST /scenarios/run with 300 samples while dragging, 1000 on release, echoed request', async () => {
    const { posts } = stub({});
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Reasons & assumptions' }));
    const slider = screen.getByLabelText(/Net leftover value per unit/);
    expect(slider).toHaveAttribute('max', '690000');
    expect(screen.getByLabelText(/Restock delay/)).toHaveAttribute('min', '-7');

    fireEvent.change(slider, { target: { value: '900000' } });
    await waitFor(() => expect(posts).toHaveLength(1));
    const first = posts[0]?.body as SimRequest;
    expect(posts[0]?.path).toBe('/scenarios/run');
    expect(first).toMatchObject({ ...REQUEST, samples: 300, param_overrides: { leftover_value_per_unit: 690_000 } });

    fireEvent.pointerUp(slider);
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toMatchObject({ samples: 1000, start: REQUEST.start, horizon_hours: 24, seed: 20261111 });
  });

  it('shows the API message when a re-run fails', async () => {
    stub({ post: () => new ApiError(400, 'PARAM_OUT_OF_RANGE', 'Giá trị hàng dư phải từ 0 đến 690.000đ.', { max: 690_000 }) });
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Reasons & assumptions' }));
    fireEvent.change(screen.getByLabelText(/Restock delay/), { target: { value: '-1' } });
    expect(await screen.findByText('Could not rerun: Giá trị hàng dư phải từ 0 đến 690.000đ.')).toBeInTheDocument();
  });

  it('no goal, operator: says a lead must set one; no form', async () => {
    stub({ campaign: { ...CAMPAIGN, campaign_id: 'CD006', goal: null }, recs: [] });
    renderAt('/campaign/CD006/compare');
    expect(await screen.findByText('Campaign CD006 needs a goal')).toBeInTheDocument();
    expect(await screen.findByText(/Ask a lead to set the campaign goal/)).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Set goal' })).not.toBeInTheDocument();
  });

  it('no goal, lead: POST /goals then POST /scenarios/run, then opens the new recommendation', async () => {
    const { posts } = stub({
      campaign: { ...CAMPAIGN, campaign_id: 'CD006', goal: null },
      recs: [],
      lead: true,
      post: (path) =>
        path === '/goals'
          ? { data: { goal_id: 'goal-NEW', scope: 'campaign', scope_id: 'CD006', kind: 'profit', label: 'x', deadline: 'x', target_units: null, params: {}, confirmed_by: 'u-lead-1', created_at: 'x', active: true, version: 1 } }
          : undefined,
    });
    const router = renderAt('/campaign/CD006/compare?district=hq&hq=comparisons');
    const form = await screen.findByRole('form', { name: 'Set goal' });
    await userEvent.click(within(form).getByRole('button', { name: 'Save goal and simulate' }));
    await waitFor(() => expect(posts.map((p) => p.path)).toEqual(['/goals', '/scenarios/run']));
    expect(posts[0]?.body).toMatchObject({ scope: 'campaign', scope_id: 'CD006', kind: 'profit', deadline: '2026-11-12T00:00:00+07:00' });
    expect(posts[1]?.body).toMatchObject({ campaign_id: 'CD006', goal_id: 'goal-NEW', samples: 1000 });
    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get('rec')).toBe('rec-REAL'));
    expect(new URLSearchParams(router.state.location.search).get('campaign')).toBe('CD006');
    expect(new URLSearchParams(router.state.location.search).get('district')).toBe('hq');
    expect(new URLSearchParams(router.state.location.search).get('hq')).toBe('comparisons');
  });

  it('goal but blocked by missing cost: lists what is missing', async () => {
    stub({
      recs: [],
      post: () =>
        new ApiError(422, 'BLOCKING_DATA_ISSUE', 'Thiếu giá vốn: THAM-XANH', {
          reasons: ['Thiếu giá vốn: THAM-XANH, TUIZIP5-G1', 'Chưa tính được lợi nhuận nên chưa so sánh các hành động'],
        }),
    });
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Run simulation (1,000 samples)' }));
    expect(await screen.findByText('Missing required data')).toBeInTheDocument();
    expect(screen.getByText('Thiếu giá vốn: THAM-XANH, TUIZIP5-G1')).toBeInTheDocument();
  });
});
