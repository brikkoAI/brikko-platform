/**
 * Type-only contract tests (Sprint 4 Поток L).
 *
 * Дополняет ``tests/schema-contract.test.ts`` (compile + runtime hybrid).
 * Здесь — *только* `expectTypeOf` assertions, vitest's pure-types режим.
 *
 * Что pin'им:
 *   1. ``LoginResponse`` shape — критично для login-redirect dance.
 *   2. ``UsageTotals`` поля совпадают по ИМЕНАМ + ТИПАМ (после TD-041 hotfix
 *      переименовали cost_kopecks → cost_kop и т.д.).
 *   3. ``ApiKey.scope`` тип строго ``'full' | 'read_only'`` — после TD-043
 *      hotfix есть `apiScopeToUi()` который mapper backend's `'write'|'read'`
 *      в frontend's `'full'|'read_only'`. Этот тест pin'ит UI-сторону.
 *   4. Error envelope shape — OpenAI-compatible `{error: {message, type, code, param}}`.
 *
 * Когда Поток O / TD-041 закроет полную OpenAPI generation → импортировать
 * ``components['schemas']['*']`` из generated и сравнить с ``Equal<>``.
 *
 * vitest config: `typecheck.enabled = true` нужен для запуска `.test-d.ts`.
 * Если не включено — vitest collects, но тесты пустые (no harm).
 */

import { describe, expectTypeOf, it } from 'vitest';
import type {
  Account,
  ApiErrorBody,
  ApiKey,
  Balance,
  Kopecks,
  LoginResponse,
  Tariff,
  UsageItem,
  UsageResponse,
  UsageTotals,
  VerifyEmailResponse,
} from '@/lib/types';

// ============================================================
// LoginResponse — фиксируем что csrf_token остаётся optional (для
// обратной совместимости с pre-Sprint 2 backend'ом).
// ============================================================

describe('LoginResponse contract', () => {
  it('has user_id, account_id, expires_at as required', () => {
    expectTypeOf<LoginResponse>().toHaveProperty('user_id');
    expectTypeOf<LoginResponse>().toHaveProperty('account_id');
    expectTypeOf<LoginResponse>().toHaveProperty('expires_at');
  });

  it('csrf_token is optional string (undefined-friendly for legacy backend)', () => {
    expectTypeOf<LoginResponse['csrf_token']>().toEqualTypeOf<string | undefined>();
  });
});

// ============================================================
// UsageTotals — после TD-041 hotfix имена полей: tokens_in/out/cached/cost_kop.
// pin'им что не «уехало» обратно к cost_kopecks/input_tokens/output_tokens.
// ============================================================

describe('UsageTotals contract (TD-041 closure)', () => {
  it('uses public field names, NOT internal ORM names', () => {
    expectTypeOf<UsageTotals>().toHaveProperty('tokens_in');
    expectTypeOf<UsageTotals>().toHaveProperty('tokens_out');
    expectTypeOf<UsageTotals>().toHaveProperty('cached_tokens');
    expectTypeOf<UsageTotals>().toHaveProperty('cost_kop');
    expectTypeOf<UsageTotals>().toHaveProperty('request_count');

    // Forbidden — старые ORM-имена не должны попадать в public type.
    expectTypeOf<UsageTotals>().not.toHaveProperty('cost_kopecks');
    expectTypeOf<UsageTotals>().not.toHaveProperty('input_tokens');
    expectTypeOf<UsageTotals>().not.toHaveProperty('output_tokens');
  });

  it('cost_kop branded as Kopecks', () => {
    expectTypeOf<UsageTotals['cost_kop']>().toEqualTypeOf<Kopecks>();
  });
});

describe('UsageItem contract', () => {
  it('date / model / key_id all nullable for "all"-aggregations', () => {
    expectTypeOf<UsageItem['date']>().toEqualTypeOf<string | null>();
    expectTypeOf<UsageItem['model']>().toEqualTypeOf<string | null>();
    expectTypeOf<UsageItem['key_id']>().toEqualTypeOf<string | null>();
  });
});

describe('UsageResponse contract', () => {
  it('flat envelope: items + totals (no `data:`/`pagination:` wrap)', () => {
    expectTypeOf<UsageResponse>().toHaveProperty('items');
    expectTypeOf<UsageResponse>().toHaveProperty('totals');
    // No data wrapper.
    expectTypeOf<UsageResponse>().not.toHaveProperty('data');
  });
});

// ============================================================
// ApiKey — TD-043 фронт-сторона
// ============================================================

describe('ApiKey scope (TD-043 closure)', () => {
  it('frontend ApiKey.scope is full|read_only — backend/UI mapper required', () => {
    type ScopeUi = ApiKey['scope'];
    // We pin THE FRONTEND TYPE; TD-043 added apiScopeToUi mapper for the
    // wire-format `'write'|'read'` from backend.
    expectTypeOf<ScopeUi>().toEqualTypeOf<'full' | 'read_only'>();
  });

  it('revoked_at is nullable string (no separate state enum)', () => {
    expectTypeOf<ApiKey['revoked_at']>().toEqualTypeOf<string | null>();
  });
});

// ============================================================
// Account
// ============================================================

describe('Account contract', () => {
  it('balance_kopecks is required number (Kopecks brand)', () => {
    expectTypeOf<Account['balance_kopecks']>().toEqualTypeOf<Kopecks>();
  });

  it('tariff is closed enum — frontend must update when backend adds new tier', () => {
    expectTypeOf<Account['tariff']>().toEqualTypeOf<Tariff>();
  });
});

// ============================================================
// Balance — minimum guarantee
// ============================================================

describe('Balance contract', () => {
  it('balance_kopecks always present (Kopecks)', () => {
    expectTypeOf<Balance>().toHaveProperty('balance_kopecks');
    expectTypeOf<Balance['balance_kopecks']>().toEqualTypeOf<Kopecks>();
  });
});

// ============================================================
// Error envelope — OpenAI-compatible
// ============================================================

describe('ApiErrorBody contract (OpenAI-compatible envelope)', () => {
  it('matches Stripe/OpenAI shape: {message, type, code?, param?}', () => {
    expectTypeOf<ApiErrorBody>().toHaveProperty('error');
    type Err = ApiErrorBody['error'];
    expectTypeOf<Err>().toHaveProperty('message');
    expectTypeOf<Err>().toHaveProperty('type');
    // code/param — optional. Без `?` тут — TS жёстко требует и кладёт runtime
    // в default null/undefined, что ломает middleware-проверки.
    expectTypeOf<Err['code']>().toEqualTypeOf<string | undefined>();
    expectTypeOf<Err['param']>().toEqualTypeOf<string | undefined>();
  });
});

// ============================================================
// VerifyEmailResponse — pin что welcome_credit_kop branded.
// ============================================================

describe('VerifyEmailResponse contract', () => {
  it('welcome_credit_kop is Kopecks-branded', () => {
    expectTypeOf<VerifyEmailResponse['welcome_credit_kop']>().toEqualTypeOf<Kopecks>();
  });

  it('verified discriminant is literal true (signals success)', () => {
    expectTypeOf<VerifyEmailResponse['verified']>().toEqualTypeOf<true>();
  });
});
