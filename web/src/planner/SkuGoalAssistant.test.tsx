import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SkuGoalAssistant } from './SkuGoalAssistant';
import type { AssistantContext } from './assistant-contract';

const context = (sku = 'SKU-A'): AssistantContext => ({
  sku: { sku, name: `Sản phẩm ${sku}`, variant: '', stockAvailable: 200, soldTodayUnits: 20,
    priceEffectiveVnd: 50000, contributionPerUnitVnd: 15000, demandGroup: 'on_dinh' },
  campaigns: [{ id: 'C1', name: 'Chiến dịch chung', skus: ['SKU-A', 'SKU-B'] }],
  dataAsOf: '2026-11-11T00:00:00Z', source: { id: 'source-1', kind: 'sku', label: 'Nguồn SKU đã nhập' },
  canPlan: true, notices: [], opening: `Bạn muốn đạt điều gì với ${sku}?`,
});
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => {
  fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/skus')) return Response.json({ items: ['SKU-A', 'SKU-B'].map((sku) => ({ sku, name: `Sản phẩm ${sku}`, variant: '', stockAvailable: 200 })), nextCursor: null, dataAsOf: context().dataAsOf });
    if (url.pathname === '/api/sale-assistant/context') return Response.json(context(url.searchParams.get('sku')!));
    throw Error(`Unexpected ${url}`);
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount() { return render(<MemoryRouter><SkuGoalAssistant key="SKU-A" sku="SKU-A" /></MemoryRouter>); }

describe('SKU context and Milo handoff', () => {
  it('keeps actual facts and sources, opening Milo with identifiers only', async () => {
    const listener = vi.fn(); window.addEventListener('workspace:milo-sku', listener);
    try {
      mount(); await screen.findByRole('heading', { name: 'Sản phẩm SKU-A' });
      expect(screen.getAllByText('Nguồn SKU đã nhập', { exact: false })).toHaveLength(4);
      expect(screen.getByText(/Budget, expected loss and economic value J always apply to the whole campaign/)).toBeInTheDocument();
      expect(listener).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Discuss with Milo' }));
      expect(listener).toHaveBeenCalledOnce();
      expect((listener.mock.calls[0]![0] as CustomEvent).detail).toEqual({ sku: 'SKU-A', screen: 'hq' });
      expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
      expect(screen.queryByLabelText('Nhu cầu của bạn')).not.toBeInTheDocument();
    } finally { window.removeEventListener('workspace:milo-sku', listener); }
  });
  it('switches Milo scope when selecting a different product', async () => {
    const listener = vi.fn(); window.addEventListener('workspace:milo-sku', listener);
    try {
      const view = mount(); await screen.findByRole('heading', { name: 'Sản phẩm SKU-A' });
      fireEvent.click(screen.getByRole('button', { name: 'Discuss with Milo' }));
      view.rerender(<MemoryRouter><SkuGoalAssistant key="SKU-B" sku="SKU-B" /></MemoryRouter>);
      await screen.findByRole('heading', { name: 'Sản phẩm SKU-B' });
      fireEvent.click(screen.getByRole('button', { name: 'Discuss with Milo' }));
      expect(listener).toHaveBeenCalledTimes(2);
      expect((listener.mock.calls[1]![0] as CustomEvent).detail.sku).toBe('SKU-B');
    } finally { window.removeEventListener('workspace:milo-sku', listener); }
  });
  it('does not invent a product or open a chat if the backend fails', async () => {
    fetchMock.mockImplementation(async () => Response.json({ error: { message: 'Backend chưa kết nối' } }, { status: 503 }));
    const listener = vi.fn(); window.addEventListener('workspace:milo-sku', listener);
    try {
      mount(); await screen.findByRole('heading', { name: 'Could not load SKU SKU-A' });
      expect(listener).not.toHaveBeenCalled();
      expect(screen.queryByRole('button', { name: /Discuss with Milo/ })).not.toBeInTheDocument();
    } finally { window.removeEventListener('workspace:milo-sku', listener); }
  });
});
