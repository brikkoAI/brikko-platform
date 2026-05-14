import { describe, expect, it, beforeEach } from 'vitest';
import { ApiClientError, accountApi, authApi, billingApi, keysApi } from '@/lib/api';
import { resetFixtures, state, TEST_ACCOUNT_ID } from '@/mocks/fixtures';

describe('ApiClientError', () => {
  it('сохраняет type, status, message, retryAfter', () => {
    const err = new ApiClientError(429, {
      type: 'rate_limit',
      message: 'Слишком много запросов',
      retry_after_ms: 1500,
    });
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe('ApiClientError');
    expect(err.type).toBe('rate_limit');
    expect(err.status).toBe(429);
    expect(err.retryAfterMs).toBe(1500);
  });
});

describe('api client integration with MSW', () => {
  beforeEach(() => resetFixtures());

  it('GET /account возвращает дефолтного пользователя через сессию по умолчанию', async () => {
    const account = await accountApi.me();
    expect(account.email).toBe('alex@studio.ru');
    expect(account.tariff).toBe('payg');
    expect(account.balance_kopecks).toBe(100_000);
  });

  it('POST /auth/signup возвращает verification_required и не залогинивает', async () => {
    const res = await authApi.signup({
      email: 'newbie@example.com',
      password: 'verystrong-pass-9',
    });
    expect(res.verification_required).toBe(true);
    expect(res.email).toBe('newbie@example.com');
  });

  it('POST /auth/signup с существующим email возвращает 409 + email_already_registered', async () => {
    await expect(
      authApi.signup({ email: 'alex@studio.ru', password: 'verystrong-pass-9' }),
    ).rejects.toMatchObject({
      type: 'email_already_registered',
      status: 409,
    });
  });

  it('POST /keys создаёт ключ и возвращает full_key один раз', async () => {
    // Дефолтная фикстура — payg-аккаунт с 2 ключами, но handler знает что
    // payg-лимит = 1. Тестовая ситуация: освобождаем место под создание нового
    // ключа, иначе мы бы валились на 402 key_limit_reached. Это не баг хендлера —
    // он корректно стережёт бизнес-правило тарифа.
    state.keys[TEST_ACCOUNT_ID] = (state.keys[TEST_ACCOUNT_ID] ?? []).slice(0, 0);
    const created = await keysApi.create({ name: 'Test', scope: 'full' });
    expect(created.full_key).toMatch(/^sk-vt-/);
    expect(created.prefix).toMatch(/^sk-vt-/);

    const list = await keysApi.list();
    const found = list.find((k) => k.id === created.id);
    expect(found).toBeDefined();
    // full_key — НЕ часть GET /keys.
    expect(found && 'full_key' in found).toBe(false);
  });

  it('DELETE /keys/{id} помечает revoked_at и возвращает 204', async () => {
    state.keys[TEST_ACCOUNT_ID] = (state.keys[TEST_ACCOUNT_ID] ?? []).slice(0, 0);
    const created = await keysApi.create({ name: 'Doomed', scope: 'full' });
    await keysApi.revoke(created.id);
    const list = await keysApi.list();
    const k = list.find((x) => x.id === created.id);
    expect(k?.revoked_at).toBeTruthy();
  });

  it('POST /billing/topup с суммой <100 ₽ — 400 validation_error', async () => {
    await expect(
      billingApi.topup({ amount_rub: 50, return_url: '/app/billing' }),
    ).rejects.toMatchObject({
      type: 'validation_error',
      status: 400,
    });
  });

  it('POST /billing/topup со 100 ₽ (новый минимум) — обновляет баланс', async () => {
    const before = await billingApi.balance();
    await billingApi.topup({ amount_rub: 100, return_url: '/app/billing' });
    const after = await billingApi.balance();
    expect(after.balance_kopecks).toBe(before.balance_kopecks + 10_000);
  });

  it('POST /billing/topup с валидной суммой обновляет баланс', async () => {
    const before = await billingApi.balance();
    await billingApi.topup({ amount_rub: 500, return_url: '/app/billing' });
    const after = await billingApi.balance();
    expect(after.balance_kopecks).toBe(before.balance_kopecks + 50_000);
  });
});
