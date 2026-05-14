/**
 * <EmptyDashboardWelcome> — Sprint 12.5+ activation play (welcome-empty-state).
 *
 * Что покрываем:
 *   - Базовый рендер: hero + 3 shortcut'а + 3 use-кейса + smart-router card.
 *   - Greeting: с именем и без (фолбэк «Привет!»).
 *   - Баланс выводится в заголовке (200 ₽ welcome-бонус).
 *   - Click «Создать» → onCreateKey() позван.
 *   - Кнопка «Скопировать» вызывает clipboard.writeText с curl и переходит в success-state.
 *   - Use-кейсы — три ссылки на /docs/cookbook/<slug>.
 *   - Smart Router card — link на /docs/smart-routing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EmptyDashboardWelcome } from '@/components/dashboard/EmptyDashboardWelcome';

describe('<EmptyDashboardWelcome>', () => {
  beforeEach(() => {
    // jsdom defines navigator.clipboard как getter-only — переопределяем через
    // Object.defineProperty (Object.assign падает с TypeError).
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('рендерит hero, 3 shortcut\'а, use-кейсы и smart-router card', () => {
    render(
      <EmptyDashboardWelcome
        balanceKopecks={20_000}
        onCreateKey={() => {}}
        userName="Максим"
      />,
    );

    // Welcome-state видим.
    expect(screen.getByTestId('empty-dashboard-welcome')).toBeInTheDocument();

    // Greeting с именем + балансом.
    expect(screen.getByText(/Привет, Максим!/)).toBeInTheDocument();
    expect(screen.getByText(/200 ₽/)).toBeInTheDocument();

    // Shortcut: создать ключ.
    expect(screen.getByTestId('welcome-shortcut-create-key')).toBeInTheDocument();
    // Shortcut: copy curl.
    expect(screen.getByTestId('welcome-shortcut-copy-curl')).toBeInTheDocument();
    // Shortcut: playground.
    expect(screen.getByTestId('welcome-shortcut-playground')).toBeInTheDocument();

    // Три use-кейса (по slug'ам из cookbook).
    expect(screen.getByTestId('welcome-usecase-contract-pii-redaction')).toBeInTheDocument();
    expect(screen.getByTestId('welcome-usecase-crm-lead-classification')).toBeInTheDocument();
    expect(screen.getByTestId('welcome-usecase-email-followup-generation')).toBeInTheDocument();

    // Smart Router link.
    const routerLink = screen.getByText(/Как работает роутер/i).closest('a');
    expect(routerLink).toHaveAttribute('href', '/docs/smart-routing');
  });

  it('без userName — greeting фолбэк «Привет!»', () => {
    render(
      <EmptyDashboardWelcome balanceKopecks={20_000} onCreateKey={() => {}} />,
    );
    expect(screen.getByText(/^Привет!/)).toBeInTheDocument();
  });

  it('click «Создать» → onCreateKey() вызван', async () => {
    const onCreateKey = vi.fn();
    const user = userEvent.setup();
    render(
      <EmptyDashboardWelcome balanceKopecks={20_000} onCreateKey={onCreateKey} />,
    );

    await user.click(screen.getByTestId('welcome-shortcut-create-key'));
    expect(onCreateKey).toHaveBeenCalledOnce();
  });

  it('click «Скопировать» → clipboard.writeText(curl) + кнопка переходит в «Скопировано»', async () => {
    render(
      <EmptyDashboardWelcome balanceKopecks={20_000} onCreateKey={() => {}} />,
    );

    // Используем fireEvent (а не userEvent) — userEvent v14 переопределяет
    // navigator.clipboard своим polyfill'ом, и наш мок mockResolvedValue не виден.
    fireEvent.click(screen.getByTestId('welcome-shortcut-copy-curl'));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledOnce();
    });
    const calls = (navigator.clipboard.writeText as unknown as { mock: { calls: string[][] } })
      .mock.calls;
    const arg = calls[0]?.[0] ?? '';
    expect(arg).toContain('curl ');
    expect(arg).toContain('/v1/chat/completions');
    expect(arg).toContain('"model": "auto:cheap"');

    // Success-state кнопки.
    await waitFor(() => {
      expect(screen.getByText(/Скопировано/)).toBeInTheDocument();
    });
  });

  it('use-кейсы ведут на /docs/cookbook/<slug> с корректными ценами', () => {
    render(
      <EmptyDashboardWelcome balanceKopecks={20_000} onCreateKey={() => {}} />,
    );

    const contract = screen.getByTestId('welcome-usecase-contract-pii-redaction');
    expect(contract).toHaveAttribute('href', '/docs/cookbook/contract-pii-redaction');
    expect(contract).toHaveTextContent(/980 ₽/);

    const crm = screen.getByTestId('welcome-usecase-crm-lead-classification');
    expect(crm).toHaveAttribute('href', '/docs/cookbook/crm-lead-classification');
    expect(crm).toHaveTextContent(/12 ₽/);

    const email = screen.getByTestId('welcome-usecase-email-followup-generation');
    expect(email).toHaveAttribute('href', '/docs/cookbook/email-followup-generation');
    expect(email).toHaveTextContent(/220 ₽/);
  });
});
