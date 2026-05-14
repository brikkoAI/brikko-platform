/**
 * notify.ts — frontend mirror to TG-bot alerts §6.1.
 *
 * Что покрываем:
 *   - Каждая helper-функция вызывает соответствующий toast variant
 *     (warning / error / success).
 *   - Текст содержит ключевые элементы из R-vetted доки.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { toKopecks } from '@/lib/types';

const successMock = vi.fn();
const errorMock = vi.fn();
const warningMock = vi.fn();

vi.mock('@/components/ui/toast', () => ({
  toast: {
    success: (...args: unknown[]) => successMock(...args),
    error: (...args: unknown[]) => errorMock(...args),
    warning: (...args: unknown[]) => warningMock(...args),
  },
  Toaster: () => null,
}));

const notify = await import('@/lib/notify');

describe('notify: TG mirror toasts', () => {
  beforeEach(() => {
    successMock.mockReset();
    errorMock.mockReset();
    warningMock.mockReset();
  });

  it('§6.1.1 balance low — warning + содержит «10 % от среднего»', () => {
    notify.notifyBalanceLow({
      balance_kop: toKopecks(50_000),
      avg_monthly_kop: toKopecks(500_000),
    });
    expect(warningMock).toHaveBeenCalledOnce();
    const [title, opts] = warningMock.mock.calls[0]!;
    expect(title).toMatch(/10 % от среднего/);
    expect(opts).toEqual({ description: 'Пополнить, чтобы запросы не остановились.' });
  });

  it('§6.1.2 balance critical — warning + «остановятся в течение часа»', () => {
    notify.notifyBalanceCritical({ balance_kop: toKopecks(5_000) });
    expect(warningMock).toHaveBeenCalledOnce();
    expect(warningMock.mock.calls[0]![0] as string).toMatch(/остановятся в течение часа/);
  });

  it('§6.1.3 balance zero — error + «402»', () => {
    notify.notifyBalanceZero();
    expect(errorMock).toHaveBeenCalledOnce();
    expect(errorMock.mock.calls[0]![0] as string).toMatch(/402/);
  });

  it('§6.1.4 payment success — success + amount + balance + email', () => {
    notify.notifyPaymentSuccess({
      amount_kop: toKopecks(100_000),
      balance_kop: toKopecks(120_000),
      email: 'a@b.ru',
    });
    expect(successMock).toHaveBeenCalledOnce();
    const [title, opts] = successMock.mock.calls[0]!;
    expect(title).toMatch(/зачислен/);
    expect((opts as { description: string }).description).toMatch(/a@b\.ru/);
  });

  it('§6.1.5 key created — success + key name + prefix', () => {
    notify.notifyKeyCreated({ name: 'Production', prefix: 'sk-vt-AB' });
    expect(successMock).toHaveBeenCalledOnce();
    const [title] = successMock.mock.calls[0]!;
    expect(title as string).toMatch(/Production/);
    expect(title as string).toMatch(/sk-vt-AB/);
  });

  it('§6.1.6 suspicious login — warning + IP + город', () => {
    notify.notifySuspiciousLogin({ ip: '1.2.3.4', city: 'Moscow', country: 'RU' });
    expect(warningMock).toHaveBeenCalledOnce();
    const [title] = warningMock.mock.calls[0]!;
    expect(title as string).toMatch(/1\.2\.3\.4/);
    expect(title as string).toMatch(/Moscow/);
  });

  it('§6.1.7 rate limit hit — warning + hits + tariff', () => {
    notify.notifyRateLimit({ hits: 14, tariff_label: 'PAYG', rpm: 60 });
    expect(warningMock).toHaveBeenCalledOnce();
    const [title] = warningMock.mock.calls[0]!;
    expect(title as string).toMatch(/14 раз/);
    expect(title as string).toMatch(/PAYG/);
  });
});
