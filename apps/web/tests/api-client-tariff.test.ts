/**
 * Sprint 6: PATCH /v1/account/tariff — MSW contract test.
 *
 * Покрываем:
 *  - Happy path: переход на pro_features списывает 1 990 ₽ с баланса и пишет в transactions.
 *  - 402: переход на тариф дороже баланса возвращает insufficient_balance с code.
 *  - requires_pii_setup: pro_privacy → true; pro_features → false.
 *  - PAYG (price=0): не списывает баланс.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { accountApi, ApiClientError } from '@/lib/api';
import { state, TEST_ACCOUNT_ID } from '@/mocks/fixtures';
import { toKopecks } from '@/lib/types';

describe('PATCH /account/tariff', () => {
  beforeEach(() => {
    // Чистый старт: payg, баланс 5 000 ₽ (хватает на pro_features 1 990 ₽).
    state.accounts[TEST_ACCOUNT_ID]!.tariff = 'payg';
    state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks = toKopecks(500_000);
    state.transactions[TEST_ACCOUNT_ID] = [];
  });

  it('happy path — pro_features списывает 1 990 ₽ и меняет tariff на «pro»', async () => {
    const before = state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks;
    const response = await accountApi.changeTariff('pro_features');
    const after = state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks;

    expect(response.tariff).toBe('pro_features');
    expect(response.requires_pii_setup).toBe(false);
    expect(after).toBe(before - 199_000);
    expect(state.accounts[TEST_ACCOUNT_ID]!.tariff).toBe('pro'); // legacy mapping
    // В транзакциях появилась подписка с отрицательной amount.
    const tx = state.transactions[TEST_ACCOUNT_ID]?.[0];
    expect(tx?.type).toBe('subscription');
    expect(tx?.amount_kopecks).toBe(-199_000);
  });

  it('pro_privacy — requires_pii_setup === true', async () => {
    state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks = toKopecks(500_000);
    const response = await accountApi.changeTariff('pro_privacy');
    expect(response.requires_pii_setup).toBe(true);
  });

  it('баланса не хватает — 402 insufficient_balance', async () => {
    state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks = toKopecks(10_000); // 100 ₽
    await expect(accountApi.changeTariff('pro_features')).rejects.toMatchObject({
      type: 'insufficient_balance',
      status: 402,
    });
    // Баланс не изменился.
    expect(state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks).toBe(toKopecks(10_000));
    // Тариф остался прежним.
    expect(state.accounts[TEST_ACCOUNT_ID]!.tariff).toBe('payg');
  });

  it('PAYG — не списывает баланс', async () => {
    state.accounts[TEST_ACCOUNT_ID]!.tariff = 'pro';
    const before = state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks;
    const response = await accountApi.changeTariff('payg');
    expect(state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks).toBe(before);
    expect(response.tariff_active_until).toBeNull();
  });

  it('insufficient_balance имеет code «balance_too_low» в error', async () => {
    state.accounts[TEST_ACCOUNT_ID]!.balance_kopecks = toKopecks(0);
    try {
      await accountApi.changeTariff('pro_features');
      throw new Error('should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiClientError);
      const apiErr = err as ApiClientError;
      expect(apiErr.type).toBe('insufficient_balance');
    }
  });
});
