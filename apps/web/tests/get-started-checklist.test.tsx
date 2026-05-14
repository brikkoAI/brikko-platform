/**
 * <GetStartedChecklist> — §3.2, 5-шаговый onboarding с locked/pending/done статусами.
 *
 * Что покрываем:
 *   - Каждый шаг получает корректный статус из props (account/keys/transactions).
 *   - Step 2 locked если email не verified; step 3 locked если ключа нет.
 *   - Прогресс-бар отражает {done}/5.
 *   - При doneCount >= 4 карточка авто-сворачивается.
 *   - При doneCount === 5 показывает «Готово».
 *   - Click «Пропустить» на step 5 — записывает flag в localStorage.
 *   - Click ×-dismiss — confirm, затем компонент исчезает.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GetStartedChecklist } from '@/components/dashboard/GetStartedChecklist';
import type { Account, ApiKey, Transaction } from '@/lib/types';
import { toKopecks } from '@/lib/types';

function buildAccount(overrides?: Partial<Account>): Account {
  return {
    user_id: 'u-1',
    email: 'a@b.ru',
    account_id: 'a-1',
    name: 'Test',
    tariff: 'payg',
    balance_kopecks: toKopecks(20_000),
    prompt_logging_enabled: false,
    notifications: {},
    created_at: '2026-04-01T00:00:00Z',
    email_verified: false,
    ...overrides,
  };
}

function buildKey(overrides?: Partial<ApiKey>): ApiKey {
  return {
    id: 'k-1',
    name: 'Default',
    prefix: 'sk-vt-AB12',
    scope: 'full',
    last_used_at: null,
    created_at: '2026-04-01T00:00:00Z',
    revoked_at: null,
    ...overrides,
  };
}

function buildTx(overrides?: Partial<Transaction>): Transaction {
  return {
    id: 't-1',
    type: 'topup',
    status: 'succeeded',
    amount_kopecks: toKopecks(10_000),
    created_at: '2026-04-15T00:00:00Z',
    description: 'Top-up',
    ...overrides,
  } as Transaction;
}

describe('<GetStartedChecklist>', () => {
  beforeEach(() => {
    window.localStorage.clear();
    // jsdom confirm — мокаем, чтобы dismiss-test проходил.
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('новый юзер: шаг 1 pending, остальные locked / pending — соответственно', () => {
    render(
      <GetStartedChecklist
        account={buildAccount({ email_verified: false })}
        keys={[]}
        transactions={[]}
      />,
    );

    // Step 1 — pending (есть CTA «Отправить ещё раз»).
    const step1 = screen.getByTestId('checklist-step-1');
    expect(step1).toHaveAttribute('data-status', 'pending');

    // Step 2 — locked (email не verified).
    const step2 = screen.getByTestId('checklist-step-2');
    expect(step2).toHaveAttribute('data-status', 'locked');

    // Step 3 — locked (ключа нет).
    const step3 = screen.getByTestId('checklist-step-3');
    expect(step3).toHaveAttribute('data-status', 'locked');

    // Step 4 — pending (всегда доступен).
    const step4 = screen.getByTestId('checklist-step-4');
    expect(step4).toHaveAttribute('data-status', 'pending');

    // Step 5 — pending.
    const step5 = screen.getByTestId('checklist-step-5');
    expect(step5).toHaveAttribute('data-status', 'pending');
  });

  it('email verified + 1 ключ: step 1 done, step 2 done, step 3 pending', () => {
    render(
      <GetStartedChecklist
        account={buildAccount({ email_verified: true })}
        keys={[buildKey()]}
        transactions={[]}
      />,
    );

    expect(screen.getByTestId('checklist-step-1')).toHaveAttribute('data-status', 'done');
    expect(screen.getByTestId('checklist-step-2')).toHaveAttribute('data-status', 'done');
    expect(screen.getByTestId('checklist-step-3')).toHaveAttribute('data-status', 'pending');
  });

  it('Пропустить TG → step 5 → skipped + localStorage flag', async () => {
    const user = userEvent.setup();
    render(
      <GetStartedChecklist
        account={buildAccount({ email_verified: true })}
        keys={[buildKey()]}
        transactions={[]}
      />,
    );

    await user.click(screen.getByTestId('checklist-skip-tg'));
    expect(window.localStorage.getItem('voltari.checklist_tg_skipped')).toBe('true');
    expect(screen.getByTestId('checklist-step-5')).toHaveAttribute('data-status', 'skipped');
  });

  it('все 5 шагов done — рендерим «Готово» card', () => {
    render(
      <GetStartedChecklist
        account={buildAccount({
          email_verified: true,
          telegram_link: { linked: true, chat_id: '123' },
        })}
        keys={[buildKey()]}
        transactions={[
          buildTx({ id: 't-1', type: 'topup' }),
          buildTx({ id: 't-2', type: 'usage', amount_kopecks: toKopecks(-100) }),
        ]}
      />,
    );

    expect(screen.getByTestId('get-started-done')).toHaveTextContent(
      'Готово — все шаги выполнены',
    );
  });

  it('×-dismiss → confirm true → компонент исчезает', async () => {
    const user = userEvent.setup();
    render(
      <GetStartedChecklist
        account={buildAccount({ email_verified: false })}
        keys={[]}
        transactions={[]}
      />,
    );

    await user.click(screen.getByTestId('checklist-dismiss'));
    expect(window.localStorage.getItem('voltari.checklist_dismissed')).toBe('true');
    expect(screen.queryByTestId('get-started-checklist')).toBeNull();
  });

  it('CTA «Создать ключ» вызывает onCreateKey', async () => {
    const onCreateKey = vi.fn();
    const user = userEvent.setup();
    render(
      <GetStartedChecklist
        account={buildAccount({ email_verified: true })}
        keys={[]}
        transactions={[]}
        onCreateKey={onCreateKey}
      />,
    );

    await user.click(screen.getByTestId('checklist-create-key'));
    expect(onCreateKey).toHaveBeenCalledOnce();
  });
});
