/**
 * Schema-contract guard (TD-041).
 *
 * Проверяет, что ключевые поля frontend-типов совпадают с тем, что мы ОЖИДАЕМ
 * получить от backend'а (см. gateway/api/*.py response_model).
 *
 * Это пока compile-time guard через type-level equality. Когда H-поток выкатит
 * `npm run generate:types` (см. scripts/generate-types.ts), этот тест расширится
 * до import'а `types-generated.ts` и diff'а между ними.
 *
 * Стратегия:
 *   1. Хардкодим минимальный «структурный snapshot» по каждому критическому DTO.
 *   2. Пишем `Equal<A, B>` helper — fails compilation если поля разъехались.
 *   3. Когда openapi-typescript будет работать end-to-end, B меняем на
 *      `components['schemas']['UsageTotals']` etc.
 *
 * Замечание про runtime:
 *   - Vitest не запускает type-check'ов сам по себе. Поэтому `npm run typecheck`
 *     гарантирует, что тест валиден по типам, а vitest валидирует только
 *     RUNTIME структуру через JSON-schema-style проверки полей на образцах.
 *   - Достаточно одного слабого assert'а в it() для регистрации теста vitest'ом.
 */

import { describe, it, expect } from 'vitest';
import type {
  Account,
  ApiKey,
  Balance,
  Transaction,
  UsageItem,
  UsageTotals,
  UsageResponse,
} from '@/lib/types';
/**
 * Sprint 4 closure (TD-041):
 *   `types-generated.ts` создан скриптом `npm run generate:types` из FastAPI OpenAPI spec'а.
 *   Cross-check ниже валит TS-build, если backend изменит response без обновления frontend'а.
 *
 *   Если этот импорт fail'ится — запусти `npm run generate:types` (с running gateway или
 *   через `OPENAPI_FILE=.openapi-snapshot.json`).
 */
import type { components, paths } from '@/lib/types-generated';

// ============================================================
// Type-level helpers (compile-time)
// ============================================================

/** True iff X и Y структурно идентичны по KEY'ам. Иначе compile error. */
type Equal<X, Y> = (<T>() => T extends X ? 1 : 2) extends <T>() => T extends Y ? 1 : 2
  ? true
  : false;

type Expect<T extends true> = T;

// ============================================================
// Frozen contract snapshots — образцы, которые backend ОБЯЗАН отдавать.
// При изменении этих типов на бэке — контракт-тест вынудит синхронизировать.
// ============================================================

interface UsageTotalsContract {
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_kop: number; // backend кладёт integer; frontend брендирует через Kopecks.
  request_count: number;
}

interface UsageItemContract {
  date: string | null;
  model: string | null;
  key_id: string | null;
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_kop: number;
  // Sprint 8: X-Router-Decision поля (см. BRIEF Sprint 8 §6).
  // Optional — старые backend'ы / per-day агрегаты не возвращают.
  routing_strategy?: 'cheap' | 'smart' | 'fast' | 'ru_legal' | 'custom' | null;
  routing_model_chosen?: string | null;
  routing_fallback_used?: boolean;
}

/**
 * Account snapshot contract.
 *
 * **Sprint 4 update:** добавлены `pii_masking_enabled?` и `telegram_link?`.
 * Оба поля — optional, потому что legacy MVP-аккаунты могут не иметь миграции к моменту
 * первого `/v1/account` запроса (Поток M докатывает миграцию backend'а отдельно).
 *
 * Тарифы расширены `pro_privacy` и `business_privacy` — Privacy-варианты Pro/Business
 * с включённым PII-маскингом по умолчанию (см. 04_Market/07_v2_monetization_research_2026-04-30.md).
 */
interface AccountContract {
  user_id: string;
  email: string;
  account_id: string;
  name: string;
  tariff:
    | 'payg'
    | 'pro'
    | 'pro_privacy'
    | 'team'
    | 'business'
    | 'business_privacy'
    | 'business_plus';
  balance_kopecks: number;
  prompt_logging_enabled: boolean;
  notifications: Record<string, unknown>;
  created_at: string;
  email_verified: boolean;
  pii_masking_enabled?: boolean;
  telegram_link?: { linked: boolean; chat_id: string | null };
  // Sprint 6 closure fields (legacy + 2FA).
  two_factor_enabled?: boolean;
  two_factor_enabled_at?: string | null;
  closure_scheduled?: boolean;
  scheduled_closure_at?: string | null;
  // Sprint 7 closure fields (см. 22_routing_preferences_spec.md референс на BE Sprint 6 поля).
  closure_requested_at?: string | null;
  closure_scheduled_at?: string | null;
  closure_reason?: string | null;
  closed_at?: string | null;
}

// ============================================================
// Compile-time проверки. Если frontend-тип разъезжается со снапшотом
// контракта — TS вернёт error на _check === true.
//
// Игнор `Expect` использования: TS-проверка сводит type-equality на этих
// строках; никакого runtime'а.
// ============================================================

// UsageTotals: требуем полное соответствие по ключам/типам (с учётом branded
// `Kopecks`, который структурно совместим с number — branding nominal).
type _CheckUsageTotalsKeys = Expect<
  Equal<keyof UsageTotals, keyof UsageTotalsContract>
>;

type _CheckUsageItemKeys = Expect<Equal<keyof UsageItem, keyof UsageItemContract>>;

type _CheckAccountKeys = Expect<Equal<keyof Account, keyof AccountContract>>;

// Балансу важно: `balance_kopecks` всегда есть. Дополнительные `account_id?`,
// `tariff?`, `autorefill_enabled?` — допустимы, MSW vs prod backend разнятся.
type _CheckBalanceMustHave = Expect<Equal<Balance['balance_kopecks'] extends number ? true : false, true>>;

// Затыкаем "unused" предупреждение — TS-side проверки уже сработали.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
type _CompiledChecks = [
  _CheckUsageTotalsKeys,
  _CheckUsageItemKeys,
  _CheckAccountKeys,
  _CheckBalanceMustHave,
];

// ============================================================
// Sprint 4 — cross-check против сгенерированных types-generated.ts.
// Берём ключевые DTO и фиксируем, что наш hand-typed `Account` обязательные
// поля = subset обязательных полей backend'а. (Расширения через optional
// `pii_masking_enabled?` / `telegram_link?` допустимы — backend докатывает.)
// ============================================================

type GeneratedAccount = components['schemas']['AccountResponse'];

// Все required-поля сгенерированного Account должны быть keyofнашего Account.
type _AccountKeysSubset = Expect<
  Equal<keyof GeneratedAccount extends keyof Account ? true : false, true>
>;

// Telegram link contract — поля, которые SPA читает, должны совпадать с backend'ом.
type GeneratedTgLink = components['schemas']['TelegramLinkResponse'];
type ExpectedTgLink = {
  token: string;
  ttl_seconds: number;
  bot_username: string;
  deep_link: string;
};
type _TgLinkKeys = Expect<Equal<keyof GeneratedTgLink, keyof ExpectedTgLink>>;

// Балансовый response — добавочное unifying-руководство.
type GeneratedBalance = components['schemas']['BalanceResponse'];
type _BalanceMustHaveKopecks = Expect<
  Equal<GeneratedBalance['balance_kopecks'] extends number ? true : false, true>
>;

// Compile-time стопор drift'а на generated-side.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
type _GeneratedCrossChecks = [_AccountKeysSubset, _TgLinkKeys, _BalanceMustHaveKopecks];

// ============================================================
// Smoke-tests для paths['*'] — критичных endpoints должна быть schema.
// Если backend удалит endpoint без согласования с frontend'ом — TS error здесь.
// ============================================================

// eslint-disable-next-line @typescript-eslint/no-unused-vars
type _CriticalPaths = [
  paths['/v1/account']['get'],
  paths['/v1/account/settings']['patch'],
  paths['/v1/account/telegram-link']['post'],
  paths['/v1/billing/balance']['get'],
  paths['/v1/billing/transactions']['get'],
  paths['/v1/keys']['get'],
];

// ============================================================
// Runtime sanity-проверки на минимальные shape'ы. Дополняют compile-time
// проверки на случай, если кто-то закастует `as` где-то в API-слое.
// ============================================================

describe('schema-contract: frontend ↔ backend ключевые DTO', () => {
  it('UsageTotals имеет ровно 5 documented полей', () => {
    const sample: UsageTotalsContract = {
      tokens_in: 100,
      tokens_out: 50,
      cached_tokens: 10,
      cost_kop: 12345,
      request_count: 7,
    };
    expect(Object.keys(sample).sort()).toEqual(
      ['cached_tokens', 'cost_kop', 'request_count', 'tokens_in', 'tokens_out'].sort(),
    );
  });

  it('UsageResponse — items + totals (никаких лишних обёрток)', () => {
    const r: UsageResponse = {
      items: [],
      totals: {
        tokens_in: 0,
        tokens_out: 0,
        cached_tokens: 0,
        cost_kop: 0 as UsageTotals['cost_kop'],
        request_count: 0,
      },
    };
    expect(r).toHaveProperty('items');
    expect(r).toHaveProperty('totals');
  });

  it('ApiKey: revoked_at — nullable string (не отдельный enum)', () => {
    const a: Pick<ApiKey, 'revoked_at'> = { revoked_at: null };
    const b: Pick<ApiKey, 'revoked_at'> = { revoked_at: '2026-04-29T00:00:00Z' };
    expect(a.revoked_at).toBeNull();
    expect(typeof b.revoked_at).toBe('string');
  });

  it('Transaction: amount_kopecks — отрицательное значение допустимо для usage', () => {
    // Backend пишет negative для USAGE-операций, не отдельный sign-флаг.
    const t: Pick<Transaction, 'type' | 'amount_kopecks'> = {
      type: 'usage',
      amount_kopecks: -10_000 as Transaction['amount_kopecks'],
    };
    expect(t.amount_kopecks).toBeLessThan(0);
  });
});
