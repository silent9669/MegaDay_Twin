import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { routes } from './routes';

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
    const nav = screen.getByRole('navigation', { name: 'Điều hướng chính' });
    const labels = within(nav)
      .getAllByRole('link')
      .map((a) => a.textContent);
    expect(labels).toEqual([
      'Tổng quan ca',
      'Chi tiết SKU',
      'So sánh lựa chọn',
      'Nhập dữ liệu',
      'Cảnh báo',
      'Nhật ký quyết định',
      'Trợ lý AI',
      'Cài đặt',
    ]);
  });

  it('renders the CD002 fryer recommendation from the 01:00 API snapshot', async () => {
    renderAt('/');
    const attention = await screen.findByRole('region', { name: /Cần xem ngay/ });
    const card = within(attention).getByText('Nồi chiên không dầu mini 2L').closest('li');
    expect(card).not.toBeNull();
    const c = within(card as HTMLElement);
    expect(c.getByText('Chỉ chạy 0–2h, 12–14h')).toBeInTheDocument();
    expect(c.getByText('Tham khảo')).toBeInTheDocument();
    expect(await c.findByText('Lợi hơn ~1,2 tr')).toBeInTheDocument();
    expect(c.getByText('Còn 76 sp · hết khoảng 16:00')).toBeInTheDocument();
    expect(c.getByRole('link', { name: /So sánh/ })).toHaveAttribute('href', '/sku/NOICHIEN2L/compare');
  });

  it('shows data clock and open alert count in the top bar', async () => {
    renderAt('/');
    expect(await screen.findByText('Dữ liệu tới 01:00')).toBeInTheDocument();
    // snapshot of the demo DB at 01:00: missing cost (21 SKU) + ads data 240 min late
    expect(await screen.findByText('Cảnh báo 2')).toBeInTheDocument();
  });

  it('SKU table: 50 rows in the bundle, real total 300', async () => {
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'Toàn bộ SKU (300)' })).toBeInTheDocument();
    expect(await screen.findByText('Đã tải 50/300 mã')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tải thêm' })).not.toBeInTheDocument();
  });

  it('navigates to a placeholder screen with title and empty state', async () => {
    const router = renderAt('/');
    await userEvent.click(screen.getByRole('link', { name: 'Nhật ký quyết định' }));
    expect(router.state.location.pathname).toBe('/decisions');
    expect(screen.getByRole('heading', { level: 1, name: 'Nhật ký quyết định' })).toBeInTheDocument();
    expect(screen.getByText('Chưa có quyết định nào')).toBeInTheDocument();
  });

  it('renders a not-found page for unknown routes', () => {
    renderAt('/khong-co');
    expect(screen.getByRole('heading', { level: 1, name: 'Không tìm thấy trang' })).toBeInTheDocument();
  });
});
