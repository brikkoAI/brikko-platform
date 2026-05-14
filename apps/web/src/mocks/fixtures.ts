/**
 * Mutable in-memory state. Поведение:
 * - shape точно соответствует API-контракту 29.04 (snake_case, копейки везде где деньги).
 * - `resetFixtures()` приводит хранилище к стартовым значениям — вызывается перед каждым тестом
 *   через server.events / `beforeEach`. В browser-е dev-режима не вызывается.
 *
 * Старт MVP: 1 пользователь Алексей, баланс 1000 ₽, 2 ключа, 30 дней usage, 3 транзакции,
 * 1 pending-invite. Это покрывает happy path всех экранов.
 */

import type {
  Account,
  ActivityEvent,
  ApiKey,
  AuthSession,
  AutorefillState,
  Balance,
  DataExportRequest,
  PendingInvite,
  RoutingPreferences,
  Seat,
  Transaction,
  UsageItem,
} from '@/lib/types';
import { toKopecks } from '@/lib/types';

const now = () => new Date().toISOString();
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

const FOUNDER_USER_ID = '11111111-1111-1111-1111-111111111111';
const ACCOUNT_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const TEAMMATE_USER_ID = '22222222-2222-2222-2222-222222222222';

interface MockUser {
  id: string;
  email: string;
  password: string;
  email_verified: boolean;
  account_id: string;
}

interface MockState {
  users: MockUser[];
  accounts: Record<string, Account>;
  keys: Record<string, ApiKey[]>; // by account_id
  fullKeys: Record<string, string>; // key.id -> raw secret (для повторной демо-выдачи в тестах)
  transactions: Record<string, Transaction[]>;
  invites: Record<string, PendingInvite[]>;
  seats: Record<string, Seat[]>;
  sessions: Record<string, { user_id: string; account_id: string; expires_at: string }>; // session_id -> session
  usage: Record<string, UsageItem[]>; // by account_id, по дням за 30 дней
  models: string[];
  // Sprint 6 ---------------------------------------------------------------
  /** Активные сессии (auth-sessions, отдельная от server-side `sessions`). */
  authSessions: Record<string, AuthSession[]>; // by account_id
  /** Pending-2FA setup'ы — secret + recovery codes, ждут verify. */
  pending2fa: Record<string, { secret: string; recovery_codes: string[] }>; // by user_id
  /** История data-export запросов (последний — для 24h-rate-limit). */
  dataExports: Record<string, DataExportRequest[]>; // by account_id
  /** Sprint 7: routing preferences per-account. */
  routingPreferences: Record<string, RoutingPreferences>; // by account_id
  /** Sprint 8: autorefill state per-account. */
  autorefill: Record<string, AutorefillState>; // by account_id
  /** Sprint 8: activity feed per-account, sorted DESC by created_at. */
  activity: Record<string, ActivityEvent[]>; // by account_id
  // Behaviour flags для тестирования error-сценариев — переключаются handler'ом или test-кодом.
  flags: {
    forceTopupFail: boolean;
    /** Если true — следующий data-export вернёт 429 (для тестов rate-limit). */
    forceDataExport429: boolean;
    /** Sprint 8: следующая bulk-операция вернёт 207 (partial failure). */
    forceBulkPartialFailure: boolean;
  };
}

const STARTING_BALANCE_KOP = toKopecks(100_000); // 1000 ₽

function buildInitialState(): MockState {
  const founder: MockUser = {
    id: FOUNDER_USER_ID,
    email: 'alex@studio.ru',
    password: 'verysecure-password-1',
    email_verified: true,
    account_id: ACCOUNT_ID,
  };

  const account: Account = {
    user_id: founder.id,
    email: founder.email,
    account_id: ACCOUNT_ID,
    name: 'Studio.ru',
    tariff: 'payg',
    balance_kopecks: STARTING_BALANCE_KOP,
    prompt_logging_enabled: false,
    notifications: {},
    created_at: daysAgo(45),
    email_verified: true,
  };

  const keys: ApiKey[] = [
    {
      id: 'k-prod-001',
      name: 'Production',
      prefix: 'sk-vt-AB12',
      scope: 'full',
      last_used_at: daysAgo(0),
      created_at: daysAgo(40),
      revoked_at: null,
    },
    {
      id: 'k-stage-002',
      name: 'Staging',
      prefix: 'sk-vt-XY99',
      scope: 'full',
      last_used_at: daysAgo(5),
      created_at: daysAgo(20),
      revoked_at: null,
    },
  ];

  const fullKeys: Record<string, string> = {
    'k-prod-001': 'sk-vt-AB12CDxx_redacted_existing_key_demo_only',
    'k-stage-002': 'sk-vt-XY99ZZxx_redacted_existing_key_demo_only',
  };

  const seats: Seat[] = [
    { user_id: founder.id, email: founder.email, role: 'owner', joined_at: daysAgo(45) },
    {
      user_id: TEAMMATE_USER_ID,
      email: 'kate@studio.ru',
      role: 'admin',
      joined_at: daysAgo(15),
    },
  ];

  const invites: PendingInvite[] = [
    {
      invite_id: 'inv-001',
      email: 'maria@studio.ru',
      role: 'member',
      invited_at: daysAgo(2),
      expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString(),
    },
  ];

  const transactions: Transaction[] = [
    {
      id: 't-welcome',
      type: 'welcome_credit',
      amount_kopecks: toKopecks(20_000),
      status: 'succeeded',
      description: 'Welcome-бонус 200 ₽',
      created_at: daysAgo(45),
      receipt_available: false,
    },
    {
      id: 't-topup-1',
      type: 'topup',
      amount_kopecks: toKopecks(100_000),
      status: 'succeeded',
      description: 'Пополнение через ЮKassa',
      created_at: daysAgo(15),
      receipt_available: true,
    },
    {
      id: 't-usage-1',
      type: 'usage',
      amount_kopecks: toKopecks(-20_000),
      status: 'succeeded',
      description: 'Списания за период 14-29 апр',
      created_at: daysAgo(1),
      receipt_available: false,
    },
  ];

  const models = [
    'gpt-5.4-mini',
    'gpt-5.4',
    'claude-sonnet-4.6',
    'claude-haiku-4.5',
    'gemini-3.1-pro',
    'yandexgpt-5.1-pro',
  ];

  // 30 дней usage с разнообразием моделей. Стабильно, не random — иначе e2e flaky.
  const usage: UsageItem[] = [];
  for (let i = 29; i >= 0; i--) {
    // Будни — больше нагрузки, выходные — меньше; всё детерминированно.
    const date = new Date(Date.now() - i * 86_400_000);
    const dow = date.getUTCDay();
    const baseLoad = dow === 0 || dow === 6 ? 0.25 : 1;
    const dayKey = date.toISOString().slice(0, 10);

    for (const model of models) {
      const factor =
        model.startsWith('gpt-5.4-mini') ? 1
          : model.startsWith('gpt-5.4') ? 0.5
          : model.startsWith('claude') ? 0.4
          : model.startsWith('gemini') ? 0.2
          : 0.15;
      const tokens_in = Math.round(2_000 * baseLoad * factor * (1 + (i % 5) * 0.1));
      const tokens_out = Math.round(800 * baseLoad * factor * (1 + (i % 3) * 0.08));
      const cached_tokens = Math.round(tokens_in * 0.15);
      const cost_per_mtok = model.includes('mini') || model.includes('haiku') ? 8
        : model.includes('5.4') ? 80
        : model.includes('claude-sonnet') ? 60
        : 40;
      const cost_kop = Math.round(((tokens_in + tokens_out) / 1_000_000) * cost_per_mtok * 100);

      usage.push({
        date: dayKey,
        model,
        key_id: null,
        tokens_in,
        tokens_out,
        cached_tokens,
        cost_kop: toKopecks(cost_kop),
      });
    }
  }

  // Активные auth-сессии для Settings → Security. Текущая (Chrome) + 2 разные device'а.
  const authSessions: AuthSession[] = [
    {
      id: 'sess-current',
      device: 'Chrome on Windows',
      ip: '95.213.16.42',
      last_active_at: now(),
      is_current: true,
    },
    {
      id: 'sess-mobile',
      device: 'Safari on iOS',
      ip: '95.213.16.42',
      last_active_at: daysAgo(1),
      is_current: false,
    },
    {
      id: 'sess-old',
      device: 'Firefox on Linux',
      ip: '178.155.4.12',
      last_active_at: daysAgo(7),
      is_current: false,
    },
  ];

  // Sprint 8: autorefill default — выключено, одна сохранённая карта (имитация
  // того, что юзер уже делал topup и согласился сохранить карту).
  const autorefill: AutorefillState = {
    enabled: false,
    threshold_kopecks: null,
    amount_kopecks: null,
    payment_method_id: null,
    saved_methods: [
      {
        id: 'pm-default-001',
        card_mask: '•••• 4242',
        brand: 'Visa',
        added_at: daysAgo(30),
        is_default: true,
      },
    ],
    failure_count: 0,
    last_failure_at: null,
    last_failure_reason: null,
  };

  // Sprint 8: activity feed — 5 событий покрывающих основные types (для UI-теста).
  const activity: ActivityEvent[] = [
    {
      id: 'act-001',
      type: 'key_created',
      summary: 'Создан ключ «Production»',
      details: 'Scope: Полный. Префикс: sk-vt-AB12.',
      created_at: now(),
      meta: { key_name: 'Production', key_prefix: 'sk-vt-AB12' },
    },
    {
      id: 'act-002',
      type: 'balance_topup',
      summary: 'Пополнение 1 000 ₽',
      details: 'ЮKassa, карта •••• 4242.',
      created_at: daysAgo(1),
      meta: { amount_kopecks: 100_000 },
    },
    {
      id: 'act-003',
      type: 'login_new_device',
      summary: 'Вход с нового устройства',
      details: 'Chrome on Windows, IP 95.213.16.42',
      created_at: daysAgo(2),
      meta: { device: 'Chrome on Windows' },
    },
    {
      id: 'act-004',
      type: 'seat_invited',
      summary: 'Приглашён kate@studio.ru',
      details: 'Роль: admin.',
      created_at: daysAgo(15),
    },
    {
      id: 'act-005',
      type: 'balance_low',
      summary: 'Баланс ниже 500 ₽',
      details: 'Осталось ~150 запросов к gpt-5.4-mini.',
      created_at: daysAgo(20),
    },
  ];

  // Sprint 7: routing preferences default — full smart routing на cheap (=
  // обратная совместимость с pre-Sprint-7 behaviour, см. §8.2 спеки).
  const routingPreferences: RoutingPreferences = {
    routing_mode: 'smart',
    routing_strategy: 'cheap',
    allowed_providers: null,
    allowed_models: null,
    preview: {
      active_models: [
        'gpt-5.4-mini',
        'claude-haiku-4.5',
        'deepseek-v3.2-chat',
        'yandexgpt-5.1-pro',
        'gigachat-2-lite',
      ],
      active_models_total: 12,
      // 800 коп/1M токенов = 8 ₽ — ориентировочная цена cheap-стратегии.
      estimated_avg_cost_kop_per_1m_tokens: 800,
      warnings: [],
    },
    available_providers: [
      { id: 'openai', label: 'OpenAI', model_count: 5 },
      { id: 'anthropic', label: 'Anthropic', model_count: 3 },
      { id: 'google', label: 'Google', model_count: 2 },
      { id: 'deepseek', label: 'DeepSeek', model_count: 1 },
      { id: 'yandex', label: 'Yandex', model_count: 2 },
      { id: 'sber', label: 'GigaChat', model_count: 2 },
    ],
    updated_at: null,
  };

  return {
    users: [founder],
    accounts: { [ACCOUNT_ID]: account },
    keys: { [ACCOUNT_ID]: keys },
    fullKeys,
    transactions: { [ACCOUNT_ID]: transactions },
    invites: { [ACCOUNT_ID]: invites },
    seats: { [ACCOUNT_ID]: seats },
    sessions: {
      // Стартовая сессия — позволяет из коробки увидеть /app без логина при dev-разработке.
      'session-default': {
        user_id: FOUNDER_USER_ID,
        account_id: ACCOUNT_ID,
        expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
      },
    },
    usage: { [ACCOUNT_ID]: usage },
    models,
    authSessions: { [ACCOUNT_ID]: authSessions },
    pending2fa: {},
    dataExports: { [ACCOUNT_ID]: [] },
    routingPreferences: { [ACCOUNT_ID]: routingPreferences },
    autorefill: { [ACCOUNT_ID]: autorefill },
    activity: { [ACCOUNT_ID]: activity },
    flags: {
      forceTopupFail: false,
      forceDataExport429: false,
      forceBulkPartialFailure: false,
    },
  };
}

export const state: MockState = buildInitialState();

export function resetFixtures(): void {
  const fresh = buildInitialState();
  state.users = fresh.users;
  state.accounts = fresh.accounts;
  state.keys = fresh.keys;
  state.fullKeys = fresh.fullKeys;
  state.transactions = fresh.transactions;
  state.invites = fresh.invites;
  state.seats = fresh.seats;
  state.sessions = fresh.sessions;
  state.usage = fresh.usage;
  state.models = fresh.models;
  state.authSessions = fresh.authSessions;
  state.pending2fa = fresh.pending2fa;
  state.dataExports = fresh.dataExports;
  state.routingPreferences = fresh.routingPreferences;
  state.autorefill = fresh.autorefill;
  state.activity = fresh.activity;
  state.flags = fresh.flags;
}

/** Хелперы для handler'ов. */
export function getBalance(account_id: string): Balance {
  const acc = state.accounts[account_id];
  return {
    balance_kopecks: acc?.balance_kopecks ?? toKopecks(0),
    autorefill_enabled: false,
  };
}

export function genId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export function genFullKey(prefix: string): { full: string; visible_prefix: string } {
  const a = Math.random().toString(36).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 4) || 'AB12';
  const visible_prefix = `sk-vt-${a}`;
  const random = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  return {
    full: `sk-vt-${a}${random}`,
    visible_prefix,
  };
}

export const TEST_SESSION_ID = 'session-default';
export const TEST_ACCOUNT_ID = ACCOUNT_ID;
export const TEST_USER_ID = FOUNDER_USER_ID;
