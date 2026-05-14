/**
 * Sprint 6: 2FA / sessions / closure / data-export — MSW contract tests.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { accountApi, authApi } from '@/lib/api';
import { state, TEST_ACCOUNT_ID, TEST_USER_ID } from '@/mocks/fixtures';

describe('2FA flow', () => {
  beforeEach(() => {
    state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled = false;
    state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled_at = null;
    delete state.pending2fa[TEST_USER_ID];
  });

  it('setup → возвращает secret, qr_url, recovery_codes (8 шт)', async () => {
    const data = await authApi.twoFactorSetup();
    expect(data.secret).toBeTruthy();
    expect(data.qr_code_url).toMatch(/^otpauth:/);
    expect(data.recovery_codes).toHaveLength(8);
    expect(state.pending2fa[TEST_USER_ID]).toBeDefined();
  });

  it('verify с правильным кодом 123456 — включает 2FA', async () => {
    await authApi.twoFactorSetup();
    await authApi.twoFactorVerify('123456');
    expect(state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled).toBe(true);
    expect(state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled_at).toBeTruthy();
  });

  it('verify с неправильным кодом — 401, 2FA остаётся выключенным', async () => {
    await authApi.twoFactorSetup();
    await expect(authApi.twoFactorVerify('999999')).rejects.toMatchObject({ status: 401 });
    expect(state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled).toBeFalsy();
  });

  it('disable с правильным кодом — отключает 2FA', async () => {
    state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled = true;
    await authApi.twoFactorDisable('123456');
    expect(state.accounts[TEST_ACCOUNT_ID]!.two_factor_enabled).toBe(false);
  });
});

describe('Active sessions', () => {
  it('list — возвращает 3 сессии с одной current', async () => {
    const list = await authApi.listSessions();
    expect(list.length).toBeGreaterThanOrEqual(3);
    expect(list.filter((s) => s.is_current)).toHaveLength(1);
  });

  it('revoke сессии — удаляет из списка', async () => {
    await authApi.revokeSession('sess-mobile');
    const list = await authApi.listSessions();
    expect(list.find((s) => s.id === 'sess-mobile')).toBeUndefined();
  });

  it('revoke текущей сессии через DELETE /sessions/:id — 403', async () => {
    await expect(authApi.revokeSession('sess-current')).rejects.toMatchObject({ status: 403 });
  });

  it('revoke all — оставляет только current', async () => {
    await authApi.revokeAllSessions();
    const list = await authApi.listSessions();
    expect(list).toHaveLength(1);
    expect(list[0]?.is_current).toBe(true);
  });
});

describe('Account closure', () => {
  beforeEach(() => {
    state.accounts[TEST_ACCOUNT_ID]!.closure_scheduled = false;
    state.accounts[TEST_ACCOUNT_ID]!.scheduled_closure_at = null;
  });

  it('close — schedules + ставит флаг в Account', async () => {
    const r = await accountApi.closeAccount('тест');
    expect(r.scheduled).toBe(true);
    expect(r.scheduled_for).toBeTruthy();
    expect(r.reason).toBe('тест');
    expect(state.accounts[TEST_ACCOUNT_ID]!.closure_scheduled).toBe(true);
  });

  it('cancelClosure — снимает флаг', async () => {
    await accountApi.closeAccount();
    await accountApi.cancelClosure();
    expect(state.accounts[TEST_ACCOUNT_ID]!.closure_scheduled).toBe(false);
    expect(state.accounts[TEST_ACCOUNT_ID]!.scheduled_closure_at).toBeNull();
  });
});

describe('Data export', () => {
  beforeEach(() => {
    state.dataExports[TEST_ACCOUNT_ID] = [];
    state.flags.forceDataExport429 = false;
  });

  it('первый запрос — pending status, 202 Accepted', async () => {
    const r = await accountApi.requestDataExport();
    expect(r.status).toBe('pending');
    expect(state.dataExports[TEST_ACCOUNT_ID]).toHaveLength(1);
  });

  it('повторный запрос в течение 24ч — 429 rate_limit', async () => {
    await accountApi.requestDataExport();
    await expect(accountApi.requestDataExport()).rejects.toMatchObject({
      type: 'rate_limit',
      status: 429,
    });
  });

  it('latestDataExport — возвращает последний запрос', async () => {
    const created = await accountApi.requestDataExport();
    const latest = await accountApi.latestDataExport();
    expect(latest?.id).toBe(created.id);
  });

  it('latestDataExport на пустой истории — null', async () => {
    const latest = await accountApi.latestDataExport();
    expect(latest).toBeNull();
  });
});

describe('Email-verify resend', () => {
  it('обычный email — 200 OK', async () => {
    const r = await authApi.emailVerifyResend('test@studio.ru');
    expect(r.ok).toBe(true);
  });

  it('пустой email — 400 validation_error', async () => {
    await expect(authApi.emailVerifyResend('')).rejects.toMatchObject({ status: 400 });
  });
});

describe('Password change v2', () => {
  it('happy path — меняет пароль', async () => {
    const user = state.users[0]!;
    user.password = 'old-password-1';
    await authApi.passwordChangeV2('old-password-1', 'new-password-2');
    expect(user.password).toBe('new-password-2');
  });

  it('неправильный текущий — 401', async () => {
    const user = state.users[0]!;
    user.password = 'old-password-1';
    await expect(
      authApi.passwordChangeV2('wrong-pass-1', 'new-password-2'),
    ).rejects.toMatchObject({ status: 401 });
  });

  it('новый пароль <8 символов — 400', async () => {
    await expect(
      authApi.passwordChangeV2('verysecure-password-1', 'short'),
    ).rejects.toMatchObject({ status: 400 });
  });

  it('новый пароль без цифры — 400', async () => {
    await expect(
      authApi.passwordChangeV2('verysecure-password-1', 'longpassword'),
    ).rejects.toMatchObject({ status: 400 });
  });
});
