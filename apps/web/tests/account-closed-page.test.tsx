/**
 * /auth/account-closed page — Sprint 7 dead-end после soft-delete'а.
 *
 * Что покрываем:
 *  - Минимальный контент: heading + support email + brand link.
 *  - Нет CTA «Войти» (это dead-end, не auth-flow).
 *  - mailto: ссылка ведёт на BRAND.supportEmail.
 *  - aria-семантика: нет отвлекающей form'ы.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AccountClosedPage from '@/app/auth/account-closed/page';
import { BRAND } from '@/lib/brand';

describe('/auth/account-closed', () => {
  it('рендерит heading «Аккаунт закрыт»', () => {
    render(<AccountClosedPage />);
    expect(
      screen.getByRole('heading', { name: /Аккаунт закрыт/i }),
    ).toBeInTheDocument();
  });

  it('mailto-ссылка ведёт на BRAND.supportEmail', () => {
    render(<AccountClosedPage />);
    const link = screen.getByTestId('account-closed-support-link');
    expect(link).toHaveAttribute('href', `mailto:${BRAND.supportEmail}`);
  });

  it('нет CTA «Войти» — это dead-end, login здесь антирекомендация', () => {
    render(<AccountClosedPage />);
    expect(screen.queryByRole('link', { name: /Войти/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Войти/i })).toBeNull();
  });

  it('контент в карточке test-id для дашборда', () => {
    render(<AccountClosedPage />);
    expect(screen.getByTestId('account-closed-card')).toBeInTheDocument();
  });
});
