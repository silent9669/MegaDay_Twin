import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SkuGoalAssistant } from './SkuGoalAssistant';
import type { AssistantContext, AssistantTurnResponse } from './assistant-contract';

const context = (sku = 'SKU-A'): AssistantContext => ({
  sku: { sku, name: `Sản phẩm ${sku}`, variant: '', stockAvailable: 200, soldTodayUnits: 20,
    priceEffectiveVnd: 50000, contributionPerUnitVnd: 15000, demandGroup: 'on_dinh' },
  campaigns: [{ id: 'C1', name: 'Chiến dịch chung', skus: ['SKU-A', 'SKU-B'] }],
  dataAsOf: '2026-11-11T00:00:00Z', source: { id: 'source-1', kind: 'sku', label: 'Nguồn SKU đã nhập' },
  canPlan: true, notices: [], opening: `Bạn muốn đạt điều gì với ${sku}?`,
});
const turn = (sku = 'SKU-A'): AssistantTurnResponse => ({
  context: context(sku), reply: `Đã nhận mục tiêu cho ${sku}. Bạn có thể kiểm tra thông số.`,
  interpretation: { method: 'rules', notices: ['Đang dùng quy tắc'], draft: {
    text: 'Bán 100 sản phẩm', kind: 'sell_target', targetUnits: 100, maxSpendVnd: 2000000,
    maxLossVnd: null, sellTargetProbability: null, minMarginalReturn: null, unresolved: [],
    start: '2026-11-11T08:00:00+07:00', deadline: '2026-11-11T12:00:00+07:00',
  } },
});
const fetchMock = vi.fn<typeof fetch>();
let turns: Record<string, unknown>[];
let runs: Record<string, unknown>[];
let contextValue: AssistantContext;
let turnResponse: () => Promise<Response>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  turns = []; runs = []; contextValue = context(); turnResponse = async () => json(turn());
  fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (input, options) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/skus')) return json({ items: ['SKU-A', 'SKU-B'].map((sku) => ({ sku, name: `Sản phẩm ${sku}`, variant: '', stockAvailable: 200 })), nextCursor: null, dataAsOf: contextValue.dataAsOf });
    if (url.pathname === '/api/sale-assistant/context') return json(url.searchParams.get('sku') === 'SKU-A' ? contextValue : context('SKU-B'));
    if (url.pathname.endsWith('/turn')) { turns.push(JSON.parse(options!.body as string)); return turnResponse(); }
    if (url.pathname === '/api/sale-planner/context') return json({ backendConfigured: true, canCreateGoals: true, referenceTime: contextValue.dataAsOf, dataAsOf: contextValue.dataAsOf, notice: null, campaigns: [{ id: 'C1', name: 'Chiến dịch chung' }, { id: 'C2', name: 'Chiến dịch khác' }, { id: 'C3', name: 'Ngoài phạm vi' }] });
    if (url.pathname.endsWith('/run')) { runs.push(JSON.parse(options!.body as string)); return json({ goalId: 'goal-1', scenario: { data: { scenario_id: 'scenario-1', blocked: false, request: { start: turn().interpretation.draft.start, horizon_hours: 4, samples: 1000, seed: 1 }, results: [], recommendation: null } } }); }
    throw Error(`Unexpected ${url}`);
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount() { render(<MemoryRouter initialEntries={['/sku-assistant?sku=SKU-A']}><SkuGoalAssistant /></MemoryRouter>); }
async function send(text = 'Bán 100 sản phẩm') {
  await screen.findByLabelText('Nhu cầu của bạn');
  fireEvent.change(screen.getByLabelText('Nhu cầu của bạn'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Gửi' }));
  await screen.findByRole('button', { name: 'Kiểm tra mục tiêu' });
}

describe('SKU goal assistant', () => {
  it('uses actual context, shows source for facts and never simulates from a chat turn', async () => {
    mount(); await send();
    expect(screen.getAllByText('Nguồn SKU đã nhập', { exact: false })).toHaveLength(4);
    expect(screen.getByText(/Ngân sách, lỗ kỳ vọng và chỉ số kinh tế J luôn thuộc toàn chiến dịch/)).toBeInTheDocument();
    expect(turns).toHaveLength(1); expect(runs).toHaveLength(0);
    expect(turns[0]).toMatchObject({ sku: 'SKU-A', messages: [{ role: 'user', content: 'Bán 100 sản phẩm' }] });
  });
  it('hands the draft to a filtered review and sends focusSku only after explicit confirmation', async () => {
    mount(); await send(); fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra mục tiêu' }));
    await waitFor(() => expect(screen.getByLabelText('Chiến dịch')).toHaveValue('C1'));
    expect(screen.queryByRole('option', { name: 'Ngoài phạm vi' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Số sản phẩm mục tiêu')).toHaveValue(100);
    const run = screen.getByRole('button', { name: 'Lưu mục tiêu và chạy mô phỏng' });
    expect(run).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Tôi xác nhận các thông số trên/ }));
    fireEvent.click(run); await screen.findByText('Phương án cho Chiến dịch chung');
    expect(runs[0]).toMatchObject({ focusSku: 'SKU-A', campaignId: 'C1', confirmed: true, draft: { targetUnits: 100 } });
  });
  it('requires campaign selection when the SKU has several memberships', async () => {
    contextValue.campaigns.push({ id: 'C2', name: 'Chiến dịch khác', skus: ['SKU-A'] });
    turnResponse = async () => json({ ...turn(), context: contextValue });
    mount(); await send(); fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra mục tiêu' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Kiểm tra lại' })).toBeEnabled());
    expect(screen.getByLabelText('Chiến dịch')).toHaveValue('');
    expect(screen.getAllByRole('option')).not.toHaveLength(0);
    expect(runs).toHaveLength(0);
  });
  it('aborts and ignores an old response when a different SKU is selected', async () => {
    let finish!: (response: Response) => void;
    turnResponse = () => new Promise((resolve) => { finish = resolve; });
    mount(); await screen.findByLabelText('Nhu cầu của bạn');
    fireEvent.change(screen.getByLabelText('Nhu cầu của bạn'), { target: { value: 'Bán 100 sản phẩm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi' }));
    await waitFor(() => expect(turns).toHaveLength(1));
    const pending = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/turn'))![1]!.signal!;
    fireEvent.click(await screen.findByRole('button', { name: 'Chọn SKU SKU-B' }));
    await screen.findByRole('heading', { name: 'Sản phẩm SKU-B' });
    expect(pending.aborted).toBe(true);
    await act(async () => { finish(json(turn())); });
    expect(screen.queryByText('Đã nhận mục tiêu cho SKU-A. Bạn có thể kiểm tra thông số.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kiểm tra mục tiêu' })).not.toBeInTheDocument();
  });
  it('preserves a failed input for retry and never shows a guessed draft', async () => {
    turnResponse = async () => json({ error: { message: 'Backend đang bận' } }, 503);
    mount(); await screen.findByLabelText('Nhu cầu của bạn');
    fireEvent.change(screen.getByLabelText('Nhu cầu của bạn'), { target: { value: 'Bán 100 sản phẩm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi' }));
    await screen.findByText('Backend đang bận');
    expect(screen.getByLabelText('Nhu cầu của bạn')).toHaveValue('Bán 100 sản phẩm');
    expect(screen.queryByRole('button', { name: 'Kiểm tra mục tiêu' })).not.toBeInTheDocument();
    expect(runs).toHaveLength(0);
  });
  it('removes the reviewed draft and confirmation when a new message is entered', async () => {
    mount(); await send(); fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra mục tiêu' }));
    await screen.findByLabelText('Chiến dịch');
    fireEvent.click(screen.getByRole('checkbox', { name: /Tôi xác nhận các thông số trên/ }));
    fireEvent.change(screen.getByLabelText('Nhu cầu của bạn'), { target: { value: 'Đổi thành 120 sản phẩm' } });
    expect(screen.queryByRole('checkbox', { name: /Tôi xác nhận các thông số trên/ })).not.toBeInTheDocument();
    expect(runs).toHaveLength(0);
  });
});
