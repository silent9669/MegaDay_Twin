import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { ComparePage,CompareIndexPage } from './ComparePage';
import { ShiftOverviewPage } from '../ShiftOverviewPage';
const routes=[{path:'/',element:<ShiftOverviewPage/>},{path:'/compare',element:<CompareIndexPage/>},{path:'/campaign/:id/compare',element:<ComparePage/>},{path:'/sku/:sku/compare',element:<ComparePage/>}];

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

// Fixtures are the real API at 01:00 on 11.11 (backend/eval/record_fixtures.py); the
// pitch numbers are checked by backend/eval/pitch_numbers.py.
describe('Compare screen · CD002 on 11.11 (API snapshot fixture)', () => {
  it('pitch numbers: schedule 0–2h, 12–14h, ~1,2 triệu better than −50% budget', async () => {
    renderAt('/campaign/CD002/compare');
    expect(await screen.findByText('Run 0–2h, 12–14h. About 1.2 million VND better than reduce budget 50%.')).toBeInTheDocument();
    expect(screen.getByText('Data through 01:00 · computed 01:00')).toBeInTheDocument();
    expect(screen.getByText(/^Saved simulation at 01:00 on 11 November/)).toBeInTheDocument();
    expect(screen.getByText('Goal: Profit through 00:00 12/11')).toBeInTheDocument();
  });

  it('tier 1: conclusion, zone bar, "better than both" line and blocked pause card', async () => {
    renderAt('/sku/NOICHIEN2L/compare');
    expect(await screen.findByText('Run 0–2h, 12–14h. About 1.2 million VND better than reduce budget 50%.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Campaign CD002' })).toBeInTheDocument();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Pausing is blocked by a flash-sale commitment');
    expect((await screen.findAllByText(/^Pausing is blocked by a flash-sale commitment\./)).length).toBeGreaterThan(0);
    expect(screen.getByText('Better than both: Run 0–2h, 12–14h.')).toBeInTheDocument();

    const cards = await within(screen.getByRole('list', { name: 'Five actions' })).findAllByRole('listitem', { name: undefined });
    const top = cards.filter((li) => li.parentElement?.getAttribute('aria-label') === 'Five actions');
    // the API also returns pause_until_restock, blocked like pause
    expect(top).toHaveLength(6);
    expect(top.filter((li) => li.textContent?.includes('Not feasible'))).toHaveLength(2);
    const pause = top.find((li) => li.textContent?.startsWith('Pause') && !li.textContent.includes('restock'));
    expect(pause).toHaveTextContent('Not feasible');
    expect(pause).toHaveTextContent('Flash-sale commitment');
    const best = top.find((li) => li.textContent?.includes('Recommended'));
    expect(best).toHaveTextContent('Run 0–2h, 12–14h');
  });

  it('tier 2: reasons with clickable numbers and the flip condition', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Reasons & assumptions' }));
    expect(screen.getByText(/contributes 9,280 VND per unit; ads break even only at iROAS ≥ 57\.2/)).toBeInTheDocument();
    const chip = screen.getByRole('button', { name: /531,000 VND/ });
    await userEvent.click(chip);
    expect(screen.getByRole('dialog', { name: 'Source: Price, cost and platform fees for NOICHIEN2L' })).toBeInTheDocument();
    expect(screen.getByText(/^At a net leftover value .* → reducing the budget by 50% is better$/)).toBeInTheDocument();
    // No restock flip in the snapshot, so fixture mode shows only the leftover slider.
    expect(screen.getByLabelText(/Net leftover value per unit/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Restock delay/)).not.toBeInTheDocument();
  });

  it('tier 3: table of 5 actions with violations, and lineage', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Audit details' }));
    const table = await screen.findByRole('table', { name: 'Five actions over the decision horizon' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(7);
    expect(within(table).getAllByText('Flash-sale commitment')).toHaveLength(2);
    expect(screen.getByText(/^fc-[0-9A-Z]+, fc-[0-9A-Z]+, fc-[0-9A-Z]+$/)).toBeInTheDocument();
    expect(screen.getByText(/^iroas-[0-9A-Z]+$/)).toBeInTheDocument();
    expect(screen.getByText('1,000 · 20261111')).toBeInTheDocument();
  });

  it('decision form is disabled in fixture mode with the reason shown', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Create decision' }));
    const form = screen.getByRole('form', { name: 'Create decision' });
    const save = within(form).getByRole('button', { name: 'Save decision draft' });
    expect(save).toBeDisabled();
    expect(within(form).getByText(/Saving decisions requires the connected backend/)).toBeInTheDocument();
    expect(within(form).getByRole('radio', { name: /^Pause ads · Flash-sale commitment/ })).toBeDisabled();
    expect(within(form).getByRole('radio', { name: 'Run 0–2h, 12–14h' })).toBeChecked();
  });
});

describe('Compare screen · page-10 ZoneBar example (fixture)', () => {
  it('page-10 example: 22,5 undetermined → drag leftover value to 20.000đ → 16,5 pause better', async () => {
    renderAt('/campaign/VIDU-T10/compare?rec=rec-510');
    expect(await screen.findByText(/Undetermined between pause ads and continue ads/)).toBeInTheDocument();
    expect(screen.getByText('Assumption-based scenario; not a validated forecast.')).toBeInTheDocument();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('22.5 units/hour');

    await userEvent.click(screen.getByRole('button', { name: 'Reasons & assumptions' }));
    fireEvent.change(screen.getByLabelText(/Net leftover value per unit/), { target: { value: '20000' } });

    const label = screen.getByRole('img').getAttribute('aria-label') ?? '';
    expect(label).toContain('Pause is better: the forecast is above the threshold.');
    expect(label).toContain('Break-even: 16.5 units/hour');
    expect(screen.getByText(/Browser break-even estimate/)).toBeInTheDocument();
    expect(screen.getByText(/A threshold was crossed: Nếu hàng dư còn giá trị từ 16\.875đ/)).toBeInTheDocument();
  });
});

describe('Compare entry and overview link', () => {
  it('/compare lists active recommendations and the illustrative cases', async () => {
    renderAt('/compare');
    expect(await screen.findByText('Illustrative model snapshots')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /Open comparison/ }).map((a) => a.getAttribute('href'));
    expect(links).toEqual(
      expect.arrayContaining(['/campaign/CD002/compare', '/campaign/VIDU-T10/compare?rec=rec-510']),
    );
  });

  it('overview "Compare" opens the compare screen for the SKU', async () => {
    const router = renderAt('/');
    const attention = await screen.findByRole('region', { name: /Needs attention/ });
    const card = within(attention).getByText('Nồi chiên không dầu mini 2L').closest('li') as HTMLElement;
    await userEvent.click(within(card).getByRole('link', { name: /Compare/ }));
    expect(router.state.location.pathname).toBe('/sku/NOICHIEN2L/compare');
    expect(await screen.findByText(/About 1.2 million VND better than reduce budget 50%/)).toBeInTheDocument();
  });
});
