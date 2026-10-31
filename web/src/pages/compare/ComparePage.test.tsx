import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { routes } from '../../routes';

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
    expect(await screen.findByText('Chỉ chạy 0–2h, 12–14h. Lợi hơn giảm ngân sách 50% khoảng 1,2 triệu đ.')).toBeInTheDocument();
    expect(screen.getByText('Dữ liệu tới 01:00 · tính lúc 01:00')).toBeInTheDocument();
    expect(screen.getByText(/^Ảnh chụp kết quả mô phỏng lúc 01:00 11\.11/)).toBeInTheDocument();
    expect(screen.getByText('Mục tiêu: Lợi nhuận tới 00:00 12/11')).toBeInTheDocument();
  });

  it('tier 1: conclusion, zone bar, "better than both" line and blocked pause card', async () => {
    renderAt('/sku/NOICHIEN2L/compare');
    expect(await screen.findByText('Chỉ chạy 0–2h, 12–14h. Lợi hơn giảm ngân sách 50% khoảng 1,2 triệu đ.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'So sánh lựa chọn cho chiến dịch CD002' })).toBeInTheDocument();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Tạm dừng bị cấm vì đã cam kết với sàn');
    expect((await screen.findAllByText(/^Tạm dừng bị cấm vì đã cam kết với sàn trong khung flash sale\./)).length).toBeGreaterThan(0);
    expect(screen.getByText('Phương án tốt hơn cả hai: Chỉ chạy 0–2h, 12–14h.')).toBeInTheDocument();

    const cards = within(screen.getByRole('list', { name: 'Năm hành động' })).getAllByRole('listitem', { name: undefined });
    const top = cards.filter((li) => li.parentElement?.getAttribute('aria-label') === 'Năm hành động');
    // the API also returns pause_until_restock, blocked like pause
    expect(top).toHaveLength(6);
    expect(top.filter((li) => li.textContent?.includes('Không làm được'))).toHaveLength(2);
    const pause = top.find((li) => li.textContent?.startsWith('Tạm dừng') && !li.textContent.includes('hàng về'));
    expect(pause).toHaveTextContent('Không làm được');
    expect(pause).toHaveTextContent('Vi phạm cam kết flash sale');
    const best = top.find((li) => li.textContent?.includes('Đề xuất'));
    expect(best).toHaveTextContent('Chỉ chạy 0–2h, 12–14h');
  });

  it('tier 2: reasons with clickable numbers and the flip condition', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Vì sao?' }));
    expect(screen.getByText(/lãi 9\.280đ; ads chỉ có lời khi iROAS ≥ 57\.2/)).toBeInTheDocument();
    const chip = screen.getByRole('button', { name: /531\.000đ/ });
    await userEvent.click(chip);
    expect(screen.getByRole('dialog', { name: 'Nguồn: Giá bán, giá vốn, phí sàn của NOICHIEN2L' })).toBeInTheDocument();
    expect(screen.getByText(/^Nếu hàng dư còn giá trị .* → Giảm ngân sách 50% tốt hơn$/)).toBeInTheDocument();
    // No restock flip in the snapshot, so fixture mode shows only the leftover slider.
    expect(screen.getByLabelText(/1 sp còn lại sau hạn/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Hàng về trễ/)).not.toBeInTheDocument();
  });

  it('tier 3: table of 5 actions with violations, and lineage', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Chi tiết kiểm tra' }));
    const table = await screen.findByRole('table', { name: 'Kết quả 5 hành động trong kỳ quyết định' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(7);
    expect(within(table).getAllByText('Vi phạm cam kết flash sale')).toHaveLength(2);
    expect(screen.getByText(/^fc-[0-9A-Z]+, fc-[0-9A-Z]+, fc-[0-9A-Z]+$/)).toBeInTheDocument();
    expect(screen.getByText(/^iroas-[0-9A-Z]+$/)).toBeInTheDocument();
    expect(screen.getByText('1.000 · 20261111')).toBeInTheDocument();
  });

  it('decision form is disabled in fixture mode with the reason shown', async () => {
    renderAt('/campaign/CD002/compare');
    await userEvent.click(await screen.findByRole('button', { name: 'Tạo quyết định' }));
    const form = screen.getByRole('form', { name: 'Tạo quyết định' });
    const save = within(form).getByRole('button', { name: 'Lưu bản nháp quyết định' });
    expect(save).toBeDisabled();
    expect(within(form).getByText(/Chế độ dữ liệu mẫu: chưa gửi được/)).toBeInTheDocument();
    expect(within(form).getByRole('radio', { name: /^Tạm dừng · Vi phạm cam kết flash sale/ })).toBeDisabled();
    expect(within(form).getByRole('radio', { name: 'Chỉ chạy 0–2h, 12–14h' })).toBeChecked();
  });
});

describe('Compare screen · page-10 ZoneBar example (fixture)', () => {
  it('page-10 example: 22,5 undetermined → drag leftover value to 20.000đ → 16,5 pause better', async () => {
    renderAt('/campaign/VIDU-T10/compare?rec=rec-510');
    expect(await screen.findByText(/Chưa phân định giữa tạm dừng và tiếp tục/)).toBeInTheDocument();
    expect(screen.getByText('Kết quả dựa trên giả định, chưa phải dự báo đã kiểm.')).toBeInTheDocument();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('ngưỡng 22,5 sp/giờ');

    await userEvent.click(screen.getByRole('button', { name: 'Vì sao?' }));
    fireEvent.change(screen.getByLabelText(/1 sp còn lại sau hạn/), { target: { value: '20000' } });

    const label = screen.getByRole('img').getAttribute('aria-label') ?? '';
    expect(label).toContain('Pause tốt hơn: cả khoảng dự báo nằm trên ngưỡng.');
    expect(label).toContain('Ngưỡng hòa vốn 16,5 sp/giờ');
    expect(screen.getByText(/Ước tính nhanh trên trình duyệt/)).toBeInTheDocument();
    expect(screen.getByText(/đã chạm điểm đổi: Nếu hàng dư còn giá trị từ 16\.875đ/)).toBeInTheDocument();
  });
});

describe('Compare entry and overview link', () => {
  it('/compare lists active recommendations and the illustrative cases', async () => {
    renderAt('/compare');
    expect(await screen.findByText('Ví dụ minh họa (dữ liệu mẫu)')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /Mở so sánh/ }).map((a) => a.getAttribute('href'));
    expect(links).toEqual(
      expect.arrayContaining(['/campaign/CD002/compare', '/campaign/VIDU-T10/compare?rec=rec-510']),
    );
  });

  it('overview "So sánh" opens the compare screen for the SKU', async () => {
    const router = renderAt('/');
    const attention = await screen.findByRole('region', { name: /Cần xem ngay/ });
    const card = within(attention).getByText('Nồi chiên không dầu mini 2L').closest('li') as HTMLElement;
    await userEvent.click(within(card).getByRole('link', { name: /So sánh/ }));
    expect(router.state.location.pathname).toBe('/sku/NOICHIEN2L/compare');
    expect(await screen.findByText(/Lợi hơn giảm ngân sách 50% khoảng 1,2 triệu đ/)).toBeInTheDocument();
  });
});
