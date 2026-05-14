/**
 * /app/billing/failure?reason=... — §5.9 payment failed page.
 *
 * Что покрываем:
 *   - Generic title + description видны всегда.
 *   - reason=card_declined → детальный текст из PAYMENT_FAILURE_DETAIL виден после toggle.
 *   - reason=unknown / отсутствует → используется fallback "unknown".
 *   - Альтернативные способы оплаты (СБП, T-Pay/SberPay, поддержка) — все 3 ссылки.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const searchParamsMock = vi.fn(() => new URLSearchParams());

vi.mock('next/navigation', () => ({
  useSearchParams: () => searchParamsMock(),
}));

const { default: PaymentFailurePage } = await import(
  '@/app/app/billing/failure/page'
);

describe('<PaymentFailurePage>', () => {
  it('generic title и description рендерятся', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams());
    render(<PaymentFailurePage />);
    expect(screen.getByText('Платёж не прошёл')).toBeInTheDocument();
    expect(
      screen.getByText('Деньги не списаны. Попробовать другой способ оплаты или другую карту.'),
    ).toBeInTheDocument();
  });

  it('reason=card_declined — после клика «Подробнее» виден детальный текст', async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=card_declined'));
    const user = userEvent.setup();
    render(<PaymentFailurePage />);
    expect(screen.queryByTestId('payment-failure-detail')).toBeNull();
    await user.click(screen.getByTestId('payment-failure-toggle-detail'));
    expect(screen.getByTestId('payment-failure-detail')).toHaveTextContent(/Банк отклонил/);
  });

  it('неизвестный reason — fallback на «unknown»', async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=garbage'));
    const user = userEvent.setup();
    render(<PaymentFailurePage />);
    await user.click(screen.getByTestId('payment-failure-toggle-detail'));
    expect(screen.getByTestId('payment-failure-detail')).toHaveTextContent(
      /Платёж отклонён без указания причины/,
    );
  });

  it('альтернативные способы оплаты — 3 ссылки', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams());
    render(<PaymentFailurePage />);
    expect(screen.getByText(/СБП/)).toBeInTheDocument();
    expect(screen.getByText(/T-Pay или SberPay/)).toBeInTheDocument();
    // Bot was renamed during the Voltari → Brikko rebrand (apr 2026).
    // The actual page now links to @BrikkoAI_bot.
    expect(screen.getByText(/@BrikkoAI_bot/)).toBeInTheDocument();
  });
});
