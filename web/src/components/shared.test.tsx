import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { CitationChip } from './CitationChip';
import { MoneyVND, Range } from './numbers';
import { Banner, UpdatingRegion } from './states';

describe('CitationChip', () => {
  it('opens a popover with source kind, tool and data time; Esc closes it', async () => {
    render(
      <MemoryRouter>
        <p>
          Hết khoảng{' '}
          <CitationChip
            citation={{
              label: 'Mô phỏng scn-2291',
              kind: 'scenario',
              id: 'scn-2291',
              tool: 'simulator · 1.000 mẫu',
              dataAsOf: '2026-11-11T01:00:00+07:00',
              computedAt: '2026-11-11T01:02:10+07:00',
              href: '#tier-3',
            }}
          >
            13:00
          </CitationChip>
        </p>
      </MemoryRouter>,
    );
    const button = screen.getByRole('button', { name: /13:00/ });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(button);
    const dialog = screen.getByRole('dialog', { name: 'Source: Mô phỏng scn-2291' });
    expect(dialog).toHaveTextContent('Simulation');
    expect(dialog).toHaveTextContent('simulator · 1.000 mẫu');
    expect(dialog).toHaveTextContent('11/11 01:00');
    expect(dialog).toHaveTextContent('01:02');
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('MoneyVND / Range', () => {
  it('formats full, short and long VND', () => {
    render(
      <>
        <MoneyVND value={531_000} />
        <MoneyVND value={-4_500_000} style="short" />
        <MoneyVND value={4_300_000} style="long" />
      </>,
    );
    expect(screen.getByText('531,000 VND')).toBeInTheDocument();
    expect(screen.getByText('−4.5 M')).toBeInTheDocument();
    expect(screen.getByText('4.3 million')).toBeInTheDocument();
  });

  it('renders a p10–p90 range with an accessible label, or nothing when a bound is missing', () => {
    const { container } = render(<Range q={{ p10: 18, p90: 31 }} />);
    expect(screen.getByLabelText('Forecast range from 18 to 31')).toHaveTextContent('(18 … 31)');
    const empty = render(<Range q={{ p10: 18 }} />);
    expect(empty.container).toBeEmptyDOMElement();
    expect(container).not.toBeEmptyDOMElement();
  });
});

describe('screen states', () => {
  it('Banner states the consequence; UpdatingRegion dims old data with the data time', () => {
    render(
      <>
        <Banner tone="neutral">Assumption-based scenario; not a validated forecast.</Banner>
        <UpdatingRegion updating dataAsOf="2026-11-11T13:05:00+07:00">
          <p>số cũ</p>
        </UpdatingRegion>
      </>,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Kết quả dựa trên giả định');
    expect(screen.getByText('Updating · data at 13:05')).toBeInTheDocument();
    expect(screen.getByText('số cũ').parentElement).toHaveClass('opacity-60');
  });
});
