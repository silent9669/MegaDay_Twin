import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { AppShell } from './components/AppShell';
import {ShiftOverviewPage} from './pages/ShiftOverviewPage';
import {NotFoundPage} from './pages/placeholders';
const routes=[{element:<AppShell/>,children:[{index:true,element:<ShiftOverviewPage/>},{path:'*',element:<NotFoundPage/>}]}];

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

describe('App shell + Shift overview (fixture mode)', () => {
  it('shows the 8 navigation entries from 09 §1', () => {
    renderAt('/');
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    const labels = within(nav)
      .getAllByRole('link')
      .map((a) => a.textContent);
    expect(labels).toEqual([
      'Overview',
      'SKU Details',
      'Comparisons',
      'Data Studio',
      'Alerts',
      'Activity Logs',
      'AI Assistant',
      'Settings',
    ]);
  });

  it('renders the CD002 fryer recommendation from the 01:00 API snapshot', async () => {
    renderAt('/');
    const attention = await screen.findByRole('region', { name: /Needs attention/ });
    const card = within(attention).getByText('Nồi chiên không dầu mini 2L').closest('li');
    expect(card).not.toBeNull();
    const c = within(card as HTMLElement);
    expect(c.getByText('Run 0–2h, 12–14h')).toBeInTheDocument();
    expect(c.getByText('Reference')).toBeInTheDocument();
    expect(await c.findByText('About 1.2 M better')).toBeInTheDocument();
    expect(c.getByText('76 units available · stockout around 16:00')).toBeInTheDocument();
    expect(c.getByRole('link', { name: /Compare/ })).toHaveAttribute('href', '/sku/NOICHIEN2L/compare');
  });

  it('shows data clock and open alert count in the top bar', async () => {
    renderAt('/');
    expect(await screen.findByText('Data through 01:00')).toBeInTheDocument();
    // snapshot of the demo DB at 01:00: missing cost (21 SKU) + ads data 240 min late
    expect(await screen.findByText('Alerts 2')).toBeInTheDocument();
  });

  it('SKU table: 50 rows in the bundle, real total 300', async () => {
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'All products (300)' })).toBeInTheDocument();
    expect(await screen.findByText('50 of 300 products')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
  });

  it('renders a not-found page for unknown routes', () => {
    renderAt('/khong-co');
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
  });
});
