import { http, HttpResponse, delay } from 'msw';
import {
  state,
  genId,
  genFullKey,
  TEST_SESSION_ID,
  TEST_ACCOUNT_ID,
  TEST_USER_ID,
} from './fixtures';
import type {
  Account,
  AccountClosureStatus,
  ActivityEvent,
  ApiKey,
  ApiKeyCreated,
  ApiKeyScope,
  AuthSession,
  AutorefillState,
  AutorefillUpdate,
  Balance,
  DataExportRequest,
  Kopecks,
  PendingInvite,
  RoutingPreferences,
  RoutingPreferencesUpdate,
  Seat,
  TariffChangeResponse,
  TariffSlug,
  Transaction,
  TransactionsPage,
  TwoFactorSetupResponse,
  UsageGroupBy,
  UsageItem,
  UsageResponse,
  UsageTotals,
} from '@/lib/types';

/**
 * Все handlers под mock-prefix /api/mock/v1 — MSW перехватывает relative URL,
 * чтобы избежать CORS и не зависеть от env. См. lib/api.ts → resolveBaseUrl().
 *
 * В vitest node-окружении (api-client.test.ts) Node fetch требует абсолютную URL,
 * поэтому из теста подсовывается NEXT_PUBLIC_API_BASE_URL=http://msw-test.local/api/mock/v1.
 * MSW в node-режиме матчит путь по абсолютному URL, поэтому handlers тоже должны
 * объявляться абсолютно. Берём absolute prefix из env (строго для тестов), иначе
 * остаёмся с relative `/api/mock/v1` — MSW в браузере и jsdom матчит по path.
 */

const TEST_ABSOLUTE_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.startsWith('http://msw-test.local')
  ? 'http://msw-test.local/api/mock/v1'
  : null;

const BASE = TEST_ABSOLUTE_BASE ?? '/api/mock/v1';

// ============================================================
// Утилиты
// ============================================================

function readSessionId(req: Request): string | null {
  const cookie = req.headers.get('cookie') ?? '';
  const match = /(?:^|;\s*)voltari_session=([^;]+)/.exec(cookie);
  return match?.[1] ?? null;
}

function getSession(req: Request): { user_id: string; account_id: string } | null {
  const id = readSessionId(req) ?? TEST_SESSION_ID; // dev: сессия по умолчанию выставлена.
  const session = state.sessions[id];
  if (!session) return null;
  if (new Date(session.expires_at).getTime() < Date.now()) {
    delete state.sessions[id];
    return null;
  }
  return { user_id: session.user_id, account_id: session.account_id };
}

function unauthorized(): HttpResponse {
  return HttpResponse.json(
    { error: { type: 'unauthorized', message: 'Сессия истекла. Войди заново.' } },
    { status: 401 },
  );
}

function validationError(message: string, details?: Record<string, unknown>): HttpResponse {
  return HttpResponse.json(
    { error: { type: 'validation_error', message, details } },
    { status: 400 },
  );
}

function tooManyRequests(retryAfterMs = 1500): HttpResponse {
  return HttpResponse.json(
    { error: { type: 'rate_limit', message: 'Слишком много запросов', retry_after_ms: retryAfterMs } },
    {
      status: 429,
      headers: { 'Retry-After': Math.ceil(retryAfterMs / 1000).toString() },
    },
  );
}

function setSessionCookie(): Headers {
  const h = new Headers();
  // Относительный путь / SameSite=Lax — этого достаточно для dev.
  // В production backend сам выдаёт Secure + HttpOnly.
  h.append(
    'Set-Cookie',
    `voltari_session=${TEST_SESSION_ID}; Path=/; SameSite=Lax`,
  );
  return h;
}

// ============================================================
// Auth
// ============================================================

const authHandlers = [
  http.post(`${BASE}/auth/signup`, async ({ request }) => {
    const body = (await request.json()) as { email?: string; password?: string };
    if (!body.email || !body.password) return validationError('Email и пароль обязательны');
    if (body.password.length < 10) return validationError('Минимум 10 символов в пароле');

    const existing = state.users.find((u) => u.email === body.email);
    if (existing) {
      return HttpResponse.json(
        {
          error: {
            type: 'email_already_registered',
            message: 'Этот email уже в Brikko',
          },
        },
        { status: 409 },
      );
    }

    const user_id = genId('u');
    const account_id = genId('acc');

    state.users.push({
      id: user_id,
      email: body.email,
      password: body.password,
      email_verified: false,
      account_id,
    });
    state.accounts[account_id] = {
      user_id,
      email: body.email,
      account_id,
      name: body.email.split('@')[0] || 'Personal',
      tariff: 'payg',
      balance_kopecks: 0 as Kopecks,
      prompt_logging_enabled: false,
      notifications: {},
      created_at: new Date().toISOString(),
      email_verified: false,
    };
    state.keys[account_id] = [];
    state.transactions[account_id] = [];
    state.invites[account_id] = [];
    state.seats[account_id] = [
      { user_id, email: body.email, role: 'owner', joined_at: new Date().toISOString() },
    ];
    state.usage[account_id] = [];

    // verify_url_dev: backend gateway в DEV/staging-режиме (когда SMTP
    // не настроен) возвращает прямую ссылку на /signup/verify-email,
    // чтобы разработчик мог пройти воронку без реального письма. MSW
    // должен поведенчески соответствовать — иначе e2e (02_signup_flow)
    // не может кликнуть [DEV] Перейти. Используем email как «токен»:
    // /v1/auth/verify-email handler ниже принимает email-как-token и
    // помечает пользователя verified. В production gateway этот ключ
    // не возвращается (см. apps/gateway/voltari_gateway/api/auth.py).
    const verify_url_dev = `/signup/verify-email?token=${encodeURIComponent(body.email)}`;

    return HttpResponse.json(
      {
        user_id,
        email: body.email,
        verification_required: true,
        verify_url_dev,
      },
      { status: 200 },
    );
  }),

  http.post(`${BASE}/auth/verify-email`, async ({ request }) => {
    const body = (await request.json()) as { token?: string };
    if (!body.token) return validationError('Токен обязателен');
    if (body.token === 'expired') {
      return HttpResponse.json(
        { error: { type: 'token_expired', message: 'Ссылка истекла' } },
        { status: 400 },
      );
    }

    // Простая модель: token = либо «default» (демо-аккаунт), либо email-уже-известного юзера.
    const user =
      body.token === 'default'
        ? state.users[0]
        : state.users.find((u) => u.email === body.token);

    if (!user) {
      return HttpResponse.json(
        { error: { type: 'token_expired', message: 'Ссылка устарела или невалидна' } },
        { status: 400 },
      );
    }

    user.email_verified = true;
    const account = state.accounts[user.account_id];
    if (!account) return validationError('Account not found');
    // Mark account as email-verified — иначе UI на /app будет считать сессию незавершённой.
    account.email_verified = true;

    const welcome_credit = 20_000 as Kopecks; // 200 ₽
    account.balance_kopecks = (account.balance_kopecks + welcome_credit) as Kopecks;

    state.transactions[user.account_id]?.unshift({
      id: genId('t'),
      type: 'welcome_credit',
      amount_kopecks: welcome_credit,
      status: 'succeeded',
      description: 'Welcome-бонус 200 ₽',
      created_at: new Date().toISOString(),
      receipt_available: false,
    });

    state.sessions[TEST_SESSION_ID] = {
      user_id: user.id,
      account_id: user.account_id,
      expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
    };

    return HttpResponse.json(
      { verified: true, welcome_credit_kop: welcome_credit },
      { status: 200, headers: setSessionCookie() },
    );
  }),

  // CSRF bootstrap. См. docs/csrf_protocol.md §1 — публичный endpoint, без auth.
  // Возвращает фиксированный mock-токен; реальный backend генерирует уникальный
  // 64-char url-safe base64. Тесты, которые проверяют ротацию токена, должны
  // override'ить этот handler через server.use().
  http.get(`${BASE}/auth/csrf`, () => {
    return HttpResponse.json(
      { csrf_token: 'mock-csrf-token-32chars-stable-aaa' },
      {
        status: 200,
        headers: {
          'set-cookie': 'vlt_csrf=mock-csrf-token-32chars-stable-aaa; Path=/; SameSite=Strict; Max-Age=2592000',
        },
      },
    );
  }),

  http.post(`${BASE}/auth/login`, async ({ request }) => {
    const body = (await request.json()) as { email?: string; password?: string };
    if (!body.email || !body.password) return validationError('Email и пароль обязательны');

    const user = state.users.find((u) => u.email === body.email);
    if (!user || user.password !== body.password) {
      return HttpResponse.json(
        { error: { type: 'invalid_credentials', message: 'Неверный email или пароль' } },
        { status: 401 },
      );
    }

    const expires_at = new Date(Date.now() + 7 * 86_400_000).toISOString();
    state.sessions[TEST_SESSION_ID] = {
      user_id: user.id,
      account_id: user.account_id,
      expires_at,
    };

    return HttpResponse.json(
      { user_id: user.id, account_id: user.account_id, expires_at },
      { status: 200, headers: setSessionCookie() },
    );
  }),

  http.post(`${BASE}/auth/logout`, () => {
    delete state.sessions[TEST_SESSION_ID];
    const h = new Headers();
    h.append('Set-Cookie', 'voltari_session=; Path=/; Max-Age=0; SameSite=Lax');
    return HttpResponse.json({ ok: true }, { status: 200, headers: h });
  }),

  http.post(`${BASE}/auth/forgot-password`, async () => {
    // Anti-enumeration: всегда 200, независимо от существования юзера.
    await delay(150);
    return HttpResponse.json({ ok: true });
  }),

  http.post(`${BASE}/auth/reset-password`, async ({ request }) => {
    const body = (await request.json()) as { token?: string; new_password?: string };
    if (!body.token || !body.new_password) return validationError('Все поля обязательны');
    if (body.new_password.length < 10) return validationError('Минимум 10 символов');
    if (body.token === 'expired') {
      return HttpResponse.json(
        { error: { type: 'token_expired', message: 'Ссылка устарела' } },
        { status: 400 },
      );
    }
    return HttpResponse.json({ ok: true });
  }),

  http.post(`${BASE}/auth/change-password`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { old?: string; new?: string };
    if (!body.old || !body.new) return validationError('Все поля обязательны');
    if (body.new.length < 10) return validationError('Минимум 10 символов');
    const user = state.users.find((u) => u.id === session.user_id);
    if (!user || user.password !== body.old) {
      return HttpResponse.json(
        { error: { type: 'invalid_credentials', message: 'Старый пароль не совпадает' } },
        { status: 401 },
      );
    }
    user.password = body.new;
    return HttpResponse.json({ ok: true });
  }),

  http.post(`${BASE}/auth/resend-verification`, async () => {
    await delay(150);
    return HttpResponse.json({ ok: true });
  }),

  // ============================================================
  // Sprint 6: email-verification resend (для expired-state на /verify-email).
  // Anti-enumeration: всегда 200, даже если email не существует.
  // ============================================================
  http.post(`${BASE}/auth/email-verify/resend`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { email?: string };
    if (!body.email) return validationError('Email обязателен');
    await delay(150);
    return HttpResponse.json({ ok: true });
  }),

  // ============================================================
  // Sprint 6: 2FA — TOTP-based (Google Authenticator)
  // ============================================================
  http.post(`${BASE}/auth/2fa/setup`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    // Стабильный mock-secret — тесты могут на него ссылаться.
    const secret = 'JBSWY3DPEHPK3PXP'; // base32 'Hello!'
    const recovery_codes = Array.from({ length: 8 }, () =>
      Math.random().toString(36).slice(2, 6).toUpperCase() + '-' + Math.random().toString(36).slice(2, 6).toUpperCase(),
    );
    state.pending2fa[session.user_id] = { secret, recovery_codes };
    const account = state.accounts[session.account_id];
    const label = encodeURIComponent(`Brikko:${account?.email ?? 'user'}`);
    const issuer = encodeURIComponent('Brikko');
    const response: TwoFactorSetupResponse = {
      secret,
      qr_code_url: `otpauth://totp/${label}?secret=${secret}&issuer=${issuer}`,
      recovery_codes,
    };
    return HttpResponse.json(response);
  }),

  http.post(`${BASE}/auth/2fa/verify`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { code?: string };
    if (!body.code || !/^\d{6}$/.test(body.code)) {
      return validationError('Введи 6-значный код');
    }
    // В моках валидным считаем «123456» либо любой код, начинающийся на «000» —
    // тесты могут проверять разные ветки.
    if (body.code !== '123456' && !body.code.startsWith('000')) {
      return HttpResponse.json(
        { error: { type: 'invalid_credentials', message: 'Неверный код. Проверь время на телефоне.' } },
        { status: 401 },
      );
    }
    const account = state.accounts[session.account_id];
    if (account) {
      account.two_factor_enabled = true;
      account.two_factor_enabled_at = new Date().toISOString();
    }
    delete state.pending2fa[session.user_id];
    return HttpResponse.json({ ok: true });
  }),

  http.post(`${BASE}/auth/2fa/disable`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { code?: string };
    if (!body.code || !/^\d{6}$/.test(body.code)) {
      return validationError('Введи 6-значный код');
    }
    if (body.code !== '123456' && !body.code.startsWith('000')) {
      return HttpResponse.json(
        { error: { type: 'invalid_credentials', message: 'Неверный код' } },
        { status: 401 },
      );
    }
    const account = state.accounts[session.account_id];
    if (account) {
      account.two_factor_enabled = false;
      account.two_factor_enabled_at = null;
    }
    return HttpResponse.json({ ok: true });
  }),

  // ============================================================
  // Sprint 6: смена пароля v2 (контракт current_password / new_password)
  // ============================================================
  http.post(`${BASE}/auth/password/change`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { current_password?: string; new_password?: string };
    if (!body.current_password || !body.new_password) {
      return validationError('Все поля обязательны');
    }
    if (body.new_password.length < 8 || !/\d/.test(body.new_password) || !/[a-zA-Zа-яА-Я]/.test(body.new_password)) {
      return validationError('Минимум 8 символов, 1 цифра и 1 буква');
    }
    const user = state.users.find((u) => u.id === session.user_id);
    if (!user || user.password !== body.current_password) {
      return HttpResponse.json(
        { error: { type: 'invalid_credentials', message: 'Текущий пароль не совпадает' } },
        { status: 401 },
      );
    }
    user.password = body.new_password;
    return HttpResponse.json({ ok: true });
  }),

  // ============================================================
  // Sprint 6: активные сессии
  // ============================================================
  http.get(`${BASE}/auth/sessions`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    // Backend wraps the list as `{items: [...]}` — listSessions unwraps to a
    // bare array on the client side. Match the production envelope here so
    // the mock and the real API agree on the shape.
    return HttpResponse.json({
      items: state.authSessions[session.account_id] ?? [],
    });
  }),
  http.delete(`${BASE}/auth/sessions/:id`, ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const id = String(params.id);
    const list = state.authSessions[session.account_id] ?? [];
    const target = list.find((s) => s.id === id);
    // Текущую сессию через этот endpoint нельзя — она revoke'ается через /auth/logout.
    if (target?.is_current) {
      return HttpResponse.json(
        { error: { type: 'forbidden', message: 'Текущая сессия закрывается через выход' } },
        { status: 403 },
      );
    }
    state.authSessions[session.account_id] = list.filter((s) => s.id !== id);
    return new HttpResponse(null, { status: 204 });
  }),
  http.delete(`${BASE}/auth/sessions`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    state.authSessions[session.account_id] = (state.authSessions[session.account_id] ?? []).filter(
      (s) => s.is_current,
    );
    return new HttpResponse(null, { status: 204 });
  }),
];

// ============================================================
// Account
// ============================================================

const accountHandlers = [
  http.get(`${BASE}/account`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();
    return HttpResponse.json<Account>(account);
  }),

  http.patch(`${BASE}/account/profile`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { name?: string; email?: string };
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();
    if (body.name !== undefined) account.name = body.name || account.email.split('@')[0] || 'Personal';
    if (body.email && body.email !== account.email) {
      const taken = state.users.some((u) => u.email === body.email && u.id !== account.user_id);
      if (taken) {
        return HttpResponse.json(
          { error: { type: 'email_already_registered', message: 'Email уже занят' } },
          { status: 409 },
        );
      }
      account.email = body.email;
    }
    return HttpResponse.json<Account>(account);
  }),

  http.patch(`${BASE}/account/settings`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as {
      prompt_logging_enabled?: boolean;
      pii_masking_enabled?: boolean;
      notifications?: Record<string, unknown>;
    };
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();
    if (typeof body.prompt_logging_enabled === 'boolean') {
      account.prompt_logging_enabled = body.prompt_logging_enabled;
    }
    // Sprint 4 / Поток M: PII-маскинг toggle. Имитируем backend-проверку тарифа —
    // только Pro Privacy / Business Privacy / Business+ могут включать.
    if (typeof body.pii_masking_enabled === 'boolean') {
      const piiTariffs = new Set(['pro_privacy', 'business_privacy', 'business_plus']);
      if (body.pii_masking_enabled && !piiTariffs.has(account.tariff)) {
        return HttpResponse.json(
          {
            error: {
              type: 'forbidden',
              message: 'PII-маскинг доступен только на тарифах Privacy. Перейди на Pro Privacy или Business Privacy.',
            },
          },
          { status: 403 },
        );
      }
      account.pii_masking_enabled = body.pii_masking_enabled;
    }
    if (body.notifications && typeof body.notifications === 'object') {
      account.notifications = { ...account.notifications, ...body.notifications };
    }
    return HttpResponse.json<Account>(account);
  }),

  // Sprint 4 / Поток M coordination: TG-бот integration stub.
  // Shape verified против OpenAPI 30.04 (components.schemas.TelegramLinkResponse).
  http.post(`${BASE}/account/telegram-link`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const token = `${Math.random().toString(36).slice(2, 10)}${Math.random().toString(36).slice(2, 10)}`;
    return HttpResponse.json({
      token,
      ttl_seconds: 300,
      bot_username: 'VoltariBot',
      deep_link: `https://t.me/VoltariBot?start=${token}`,
    });
  }),

  http.delete(`${BASE}/account/telegram-link`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const account = state.accounts[session.account_id];
    if (account) {
      account.telegram_link = { linked: false, chat_id: null };
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(`${BASE}/account/seats`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    return HttpResponse.json<Seat[]>(state.seats[session.account_id] ?? []);
  }),

  http.get(`${BASE}/account/invites`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    return HttpResponse.json<PendingInvite[]>(state.invites[session.account_id] ?? []);
  }),

  http.post(`${BASE}/account/seats/invite`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { email?: string; role?: 'admin' | 'member' };
    if (!body.email || !body.role) return validationError('Email и роль обязательны');

    const existingSeat = state.seats[session.account_id]?.find((s) => s.email === body.email);
    if (existingSeat) {
      return HttpResponse.json(
        { error: { type: 'invite_already_member', message: 'Этот email уже в команде' } },
        { status: 409 },
      );
    }

    // Лимит мест на тарифе PAYG = 1. Эмулируем 402-семантику через свой тип.
    const tariff = state.accounts[session.account_id]?.tariff;
    const seats = state.seats[session.account_id]?.length ?? 0;
    const seatLimit =
      tariff === 'payg' ? 1 : tariff === 'pro' ? 5 : tariff === 'team' ? 15 : 50;
    if (seats + (state.invites[session.account_id]?.length ?? 0) >= seatLimit) {
      return HttpResponse.json(
        {
          error: {
            type: 'seat_limit_reached',
            message: `На текущем тарифе доступно ${seatLimit} мест. Перейди на следующий, чтобы пригласить ещё.`,
          },
        },
        { status: 402 },
      );
    }

    const invite: PendingInvite = {
      invite_id: genId('inv'),
      email: body.email,
      role: body.role,
      invited_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
    };
    state.invites[session.account_id] = [...(state.invites[session.account_id] ?? []), invite];
    return HttpResponse.json<PendingInvite>(invite, { status: 200 });
  }),

  http.delete(`${BASE}/account/seats/:user_id`, ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const target = String(params.user_id);
    const seats = state.seats[session.account_id] ?? [];
    state.seats[session.account_id] = seats.filter((s) => s.user_id !== target);
    return HttpResponse.json({ ok: true });
  }),

  http.delete(`${BASE}/account/invites/:invite_id`, ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const target = String(params.invite_id);
    state.invites[session.account_id] = (state.invites[session.account_id] ?? []).filter(
      (i) => i.invite_id !== target,
    );
    return HttpResponse.json({ ok: true });
  }),

  // ============================================================
  // Sprint 6: смена тарифа.
  //
  // Backend списывает первое-месячное-значение с баланса (предоплата).
  // Если баланса не хватает — 402 с code `balance_too_low`.
  // ============================================================
  http.patch(`${BASE}/account/tariff`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { tariff?: TariffSlug };
    if (!body.tariff) return validationError('Тариф обязателен');

    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();

    // Цены месячной подписки — должны совпадать с TARIFF_CATALOG в TariffCard.tsx.
    const PRICES_KOP: Record<TariffSlug, number> = {
      payg: 0,
      pro_features: 199_000, // 1 990 ₽
      pro_privacy: 279_000, // 2 790 ₽
      team: 599_000, // 5 990 ₽
      business: 1_999_000, // 19 990 ₽
      business_plus: 10_000_000, // 100 000 ₽
    };
    const requiresPii = body.tariff === 'pro_privacy' || body.tariff === 'business_plus';

    const cost = PRICES_KOP[body.tariff] ?? 0;
    if (cost > 0 && account.balance_kopecks < cost) {
      return HttpResponse.json(
        {
          error: {
            type: 'insufficient_balance',
            code: 'balance_too_low',
            message: `Недостаточно средств: нужно ${(cost / 100).toLocaleString('ru-RU')} ₽, на балансе ${(account.balance_kopecks / 100).toLocaleString('ru-RU')} ₽`,
          },
        },
        { status: 402 },
      );
    }

    if (cost > 0) {
      account.balance_kopecks = (account.balance_kopecks - cost) as Kopecks;
      state.transactions[session.account_id]?.unshift({
        id: genId('t'),
        type: 'subscription',
        amount_kopecks: -cost as Kopecks,
        status: 'succeeded',
        description: `Подписка ${body.tariff} — месяц`,
        created_at: new Date().toISOString(),
        receipt_available: true,
      });
    }

    // Backend Account.tariff использует legacy 'pro' для pro_features. Mapping здесь.
    const legacyTariff =
      body.tariff === 'pro_features' ? 'pro' : body.tariff;
    account.tariff = legacyTariff as Account['tariff'];
    const activeUntil =
      cost > 0 ? new Date(Date.now() + 30 * 86_400_000).toISOString() : null;

    const response: TariffChangeResponse = {
      tariff: body.tariff,
      tariff_active_until: activeUntil,
      requires_pii_setup: requiresPii,
    };
    return HttpResponse.json(response);
  }),

  // ============================================================
  // Sprint 6: data-export (152-ФЗ + GDPR-style)
  // ============================================================
  http.post(`${BASE}/account/data-export`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();

    if (state.flags.forceDataExport429) {
      return HttpResponse.json(
        { error: { type: 'rate_limit', message: 'Запрос уже отправлен в последние 24 часа.' } },
        { status: 429 },
      );
    }

    // Real-rate-limit: проверяем были ли запросы за последние 24 часа.
    const exports = state.dataExports[session.account_id] ?? [];
    const last = exports[0];
    if (last && Date.now() - new Date(last.requested_at).getTime() < 24 * 3_600_000) {
      return HttpResponse.json(
        {
          error: {
            type: 'rate_limit',
            message: 'Уже есть запрос за последние 24 часа. Подожди или используй прежнюю ссылку.',
          },
        },
        { status: 429 },
      );
    }

    const req: DataExportRequest = {
      id: genId('exp'),
      status: 'pending',
      requested_at: new Date().toISOString(),
      download_url: null,
      expires_at: null,
    };
    state.dataExports[session.account_id] = [req, ...exports];
    return HttpResponse.json<DataExportRequest>(req, { status: 202 });
  }),

  http.get(`${BASE}/account/data-export/latest`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const last = (state.dataExports[session.account_id] ?? [])[0] ?? null;
    return HttpResponse.json(last);
  }),

  // ============================================================
  // Sprint 7: список всех экспортов (для История экспортов).
  //
  // Возвращаем ВСЕ запросы текущего аккаунта, отсортированные по requested_at DESC.
  // Limit на frontend'е — backend всё равно фильтрует expired'ы автоматически.
  // ============================================================
  http.get(`${BASE}/account/data-export`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const list = state.dataExports[session.account_id] ?? [];
    return HttpResponse.json<DataExportRequest[]>(list);
  }),

  // ============================================================
  // Sprint 7: статус конкретного экспорта (для polling'а).
  //
  // Behaviour: deterministic state-machine на основе возраста запроса:
  //   < 5s   → pending
  //   < 15s  → processing
  //   < 7d   → ready (с download_url)
  //   ≥ 7d   → expired (download_url=null)
  // Тесты могут override'ить через flags / setup-фикстуры.
  // ============================================================
  http.get(`${BASE}/account/data-export/:id`, ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const id = String(params.id);
    const list = state.dataExports[session.account_id] ?? [];
    const item = list.find((e) => e.id === id);
    if (!item) {
      return HttpResponse.json(
        { error: { type: 'not_found', message: 'Запрос не найден' } },
        { status: 404 },
      );
    }
    // Если статус уже terminal — возвращаем как есть. Иначе считаем по возрасту.
    if (item.status === 'ready' || item.status === 'failed' || item.status === 'expired') {
      return HttpResponse.json<DataExportRequest>(item);
    }
    const ageMs = Date.now() - new Date(item.requested_at).getTime();
    if (ageMs < 5_000) {
      item.status = 'pending';
    } else if (ageMs < 15_000) {
      item.status = 'processing';
    } else if (ageMs < 7 * 86_400_000) {
      item.status = 'ready';
      item.download_url = `https://export.brikko.ai/${item.id}.zip`;
      item.expires_at = new Date(Date.now() + 6 * 86_400_000).toISOString();
      item.finished_at = new Date().toISOString();
    } else {
      item.status = 'expired';
      item.download_url = null;
    }
    return HttpResponse.json<DataExportRequest>(item);
  }),

  // ============================================================
  // Sprint 6: закрытие аккаунта
  // ============================================================
  http.post(`${BASE}/account/close`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();
    const body = (await request.json().catch(() => ({}))) as { reason?: string };

    const requestedAt = new Date().toISOString();
    const scheduledAt = new Date(Date.now() + 30 * 86_400_000).toISOString();

    // Sprint 6 legacy-поля (для обратной совместимости со старыми тестами).
    account.closure_scheduled = true;
    account.scheduled_closure_at = scheduledAt;
    // Sprint 7 — расширенные поля по спеке.
    account.closure_requested_at = requestedAt;
    account.closure_scheduled_at = scheduledAt;
    account.closure_reason = body.reason ?? null;
    account.closed_at = null;

    const response: AccountClosureStatus = {
      scheduled: true,
      scheduled_for: scheduledAt,
      reason: body.reason ?? null,
    };
    return HttpResponse.json<AccountClosureStatus>(response);
  }),

  http.delete(`${BASE}/account/close`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();

    account.closure_scheduled = false;
    account.scheduled_closure_at = null;
    account.closure_requested_at = null;
    account.closure_scheduled_at = null;
    account.closure_reason = null;

    const response: AccountClosureStatus = {
      scheduled: false,
      scheduled_for: null,
    };
    return HttpResponse.json<AccountClosureStatus>(response);
  }),

  // ============================================================
  // Sprint 7: routing preferences (per-account smart-router policy).
  //
  // Спека: 02_Product/v1.5/22_routing_preferences_spec.md.
  //
  // Mock-валидация:
  //   - routing_strategy === 'custom' → allowed_providers непустой массив.
  //   - routing_strategy !== 'custom' → allowed_providers/_models должны быть null.
  // Backend делает то же на Pydantic-уровне — мокам важно матчить поведение.
  // ============================================================
  http.get(`${BASE}/account/routing-preferences`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const prefs = state.routingPreferences[session.account_id];
    if (!prefs) return unauthorized();
    return HttpResponse.json<RoutingPreferences>(prefs);
  }),

  // ============================================================
  // Sprint 8: Auto-refill v2 (per-account, multi-card).
  //
  // Спека: BRIEF Sprint 8 §1.
  // ============================================================
  http.get(`${BASE}/account/autorefill`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const cur = state.autorefill[session.account_id];
    if (!cur) return unauthorized();
    return HttpResponse.json<AutorefillState>(cur);
  }),

  http.put(`${BASE}/account/autorefill`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as AutorefillUpdate;
    const cur = state.autorefill[session.account_id];
    if (!cur) return unauthorized();

    if (body.enabled) {
      if (!body.threshold_kopecks || body.threshold_kopecks <= 0) {
        return validationError('Порог должен быть > 0');
      }
      if (!body.amount_kopecks || body.amount_kopecks < 10_000) {
        return validationError('Минимум 100 ₽ за пополнение');
      }
      if (body.threshold_kopecks > body.amount_kopecks) {
        return validationError('Порог не может быть больше суммы пополнения — будет бесконечный цикл');
      }
      const method = cur.saved_methods.find((m) => m.id === body.payment_method_id);
      if (!method) {
        return validationError('Карта не найдена в saved_methods');
      }
    }

    const next: AutorefillState = {
      ...cur,
      enabled: body.enabled,
      threshold_kopecks: body.enabled ? body.threshold_kopecks : null,
      amount_kopecks: body.enabled ? body.amount_kopecks : null,
      payment_method_id: body.enabled ? body.payment_method_id : null,
      // При сохранении — failure_count сбрасывается (юзер обновил карту/настройки).
      failure_count: 0,
      last_failure_at: null,
      last_failure_reason: null,
    };
    state.autorefill[session.account_id] = next;
    return HttpResponse.json<AutorefillState>(next);
  }),

  http.post(`${BASE}/account/autorefill/disable`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const cur = state.autorefill[session.account_id];
    if (!cur) return unauthorized();
    const next: AutorefillState = {
      ...cur,
      enabled: false,
      threshold_kopecks: null,
      amount_kopecks: null,
      payment_method_id: null,
      failure_count: 0,
      last_failure_at: null,
      last_failure_reason: null,
    };
    state.autorefill[session.account_id] = next;
    return HttpResponse.json<AutorefillState>(next);
  }),

  // ============================================================
  // Sprint 8: Activity feed.
  //
  // Спека: BRIEF Sprint 8 §4. Возвращаем последние N (default 10) событий.
  // ============================================================
  http.get(`${BASE}/account/activity`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get('limit') ?? '10');
    const all = state.activity[session.account_id] ?? [];
    return HttpResponse.json<ActivityEvent[]>(all.slice(0, limit));
  }),

  http.put(`${BASE}/account/routing-preferences`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as RoutingPreferencesUpdate;

    if (body.routing_mode !== 'manual' && body.routing_mode !== 'smart') {
      return validationError('routing_mode должен быть manual или smart');
    }
    const validStrategies = new Set(['cheap', 'smart', 'fast', 'ru_legal', 'custom']);
    if (!validStrategies.has(body.routing_strategy)) {
      return validationError(`unknown routing_strategy: ${body.routing_strategy}`);
    }
    if (body.routing_strategy === 'custom') {
      if (!Array.isArray(body.allowed_providers) || body.allowed_providers.length === 0) {
        return validationError('Custom strategy требует ≥ 1 провайдера в allowed_providers');
      }
      const validProviders = new Set(['openai', 'anthropic', 'google', 'deepseek', 'yandex', 'sber']);
      const unknown = body.allowed_providers.find((p) => !validProviders.has(p));
      if (unknown) {
        return validationError(`unknown provider: ${unknown}`);
      }
    } else {
      if (body.allowed_providers !== null || body.allowed_models !== null) {
        return validationError(
          'allowed_providers/_models должны быть null для не-custom стратегий',
        );
      }
    }

    const current = state.routingPreferences[session.account_id];
    if (!current) return unauthorized();

    // Считаем preview по новому filter'у. В моках упрощаем: пресет → известные модели.
    const preview = computeMockPreview(body, current.available_providers);

    const next: RoutingPreferences = {
      ...current,
      routing_mode: body.routing_mode,
      routing_strategy: body.routing_strategy,
      allowed_providers: body.allowed_providers,
      allowed_models: body.allowed_models,
      preview,
      updated_at: new Date().toISOString(),
    };
    state.routingPreferences[session.account_id] = next;
    return HttpResponse.json<RoutingPreferences>(next);
  }),
];

/**
 * Mock preview-калькулятор — детерминированный.
 *
 * Имитирует backend §3.4: top-5 моделей + средняя weighted-цена. Тесты
 * могут проверять что preview меняется при смене стратегии.
 */
function computeMockPreview(
  upd: RoutingPreferencesUpdate,
  providers: RoutingPreferences['available_providers'],
): RoutingPreferences['preview'] {
  // Default-каталог моделей по провайдерам — упрощённо.
  const catalog: Record<string, { id: string; price: number }[]> = {
    openai: [
      { id: 'gpt-5.4-mini', price: 800 },
      { id: 'gpt-5.4', price: 6000 },
      { id: 'gpt-5', price: 12000 },
      { id: 'o4-mini', price: 4000 },
      { id: 'o3', price: 18000 },
    ],
    anthropic: [
      { id: 'claude-haiku-4.5', price: 900 },
      { id: 'claude-sonnet-4.6', price: 6500 },
      { id: 'claude-opus-4.7', price: 22000 },
    ],
    google: [
      { id: 'gemini-3-flash', price: 700 },
      { id: 'gemini-3.1-pro', price: 5500 },
    ],
    deepseek: [{ id: 'deepseek-v3.2-chat', price: 600 }],
    yandex: [
      { id: 'yandexgpt-5-lite', price: 700 },
      { id: 'yandexgpt-5.1-pro', price: 4000 },
    ],
    sber: [
      { id: 'gigachat-2-lite', price: 700 },
      { id: 'gigachat-2-pro', price: 4500 },
    ],
  };

  let allowedProviders = providers.map((p) => p.id);
  if (upd.routing_strategy === 'ru_legal') {
    allowedProviders = ['yandex', 'sber'];
  } else if (upd.routing_strategy === 'custom' && upd.allowed_providers) {
    allowedProviders = upd.allowed_providers;
  }

  let pool = allowedProviders.flatMap((p) => catalog[p] ?? []);
  if (upd.routing_strategy === 'custom' && upd.allowed_models) {
    pool = pool.filter((m) => upd.allowed_models!.includes(m.id));
  }

  // Сортируем по цене, top-5.
  pool.sort((a, b) => a.price - b.price);
  const top5 = pool.slice(0, 5);
  const avg =
    top5.length > 0
      ? Math.round(top5.reduce((s, m) => s + m.price, 0) / top5.length)
      : 0;

  const warnings: string[] = [];
  if (upd.routing_strategy === 'custom' && allowedProviders.length === 1) {
    warnings.push('failover_disabled_single_provider');
  }

  return {
    active_models: top5.map((m) => m.id),
    active_models_total: pool.length,
    estimated_avg_cost_kop_per_1m_tokens: avg,
    warnings,
  };
}

// ============================================================
// Keys
// ============================================================

const keysHandlers = [
  // ============================================================
  // Sprint 8: bulk operations.
  //
  // ВАЖНО: эти handler'ы должны идти ДО `http.patch/delete/:id`, иначе
  // динамический `:id` route поймает `bulk-revoke` как id (в MSW порядок
  // имеет значение для конфликтующих путей).
  // ============================================================
  http.post(`${BASE}/keys/bulk-revoke`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { key_ids?: string[] };
    if (!Array.isArray(body.key_ids) || body.key_ids.length === 0) {
      return validationError('key_ids must be non-empty array');
    }
    if (state.flags.forceBulkPartialFailure) {
      return HttpResponse.json(
        {
          error: {
            type: 'server_error',
            message: 'Часть ключей не удалось отозвать. Попробуй ещё раз.',
          },
        },
        { status: 207 },
      );
    }
    const list = state.keys[session.account_id] ?? [];
    const revokedAt = new Date().toISOString();
    const revoked: string[] = [];
    for (const id of body.key_ids) {
      const k = list.find((x) => x.id === id);
      if (k && !k.revoked_at) {
        k.revoked_at = revokedAt;
        revoked.push(id);
      }
    }
    return HttpResponse.json({ revoked });
  }),

  http.post(`${BASE}/keys/bulk-rotate`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { key_ids?: string[] };
    if (!Array.isArray(body.key_ids) || body.key_ids.length === 0) {
      return validationError('key_ids must be non-empty array');
    }
    if (state.flags.forceBulkPartialFailure) {
      return HttpResponse.json(
        {
          error: {
            type: 'server_error',
            message: 'Часть ключей не удалось ротировать. Попробуй ещё раз.',
          },
        },
        { status: 207 },
      );
    }
    const list = state.keys[session.account_id] ?? [];
    const rotated: Array<{ id: string; full_key: string; prefix: string; scope: 'read' | 'write' }> = [];
    for (const id of body.key_ids) {
      const k = list.find((x) => x.id === id);
      if (k && !k.revoked_at) {
        // Rotate = revoke старый + сгенерить новый с тем же именем/scope.
        // Возвращаем pair {старый id → новый full_key} — UI показывает «новый ключ X
        // выдан, старый отозван». В моках просто меняем prefix у существующего ключа.
        const newRandom = Math.random().toString(36).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 4) || 'XYZW';
        const newFullKey = `sk-vt-${newRandom}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
        k.prefix = `sk-vt-${newRandom}`;
        state.fullKeys[k.id] = newFullKey;
        rotated.push({
          id: k.id,
          full_key: newFullKey,
          prefix: k.prefix,
          scope: k.scope === 'read_only' ? 'read' : 'write',
        });
      }
    }
    return HttpResponse.json({ rotated });
  }),

  http.get(`${BASE}/keys`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    return HttpResponse.json<ApiKey[]>(state.keys[session.account_id] ?? []);
  }),

  http.post(`${BASE}/keys`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { name?: string; scope?: ApiKeyScope };
    if (!body.name?.trim()) return validationError('Имя ключа обязательно');

    const tariff = state.accounts[session.account_id]?.tariff;
    const limit = tariff === 'payg' ? 1 : tariff === 'pro' ? 5 : tariff === 'team' ? 20 : 100;
    const activeCount = (state.keys[session.account_id] ?? []).filter((k) => !k.revoked_at).length;
    if (activeCount >= limit) {
      return HttpResponse.json(
        {
          error: {
            type: 'key_limit_reached',
            message: `Лимит ${limit} активных ключей на текущем тарифе. Удали неиспользуемые или перейди на следующий тариф.`,
          },
        },
        { status: 402 },
      );
    }

    const { full, visible_prefix } = genFullKey('sk-vt');
    const key: ApiKey = {
      id: genId('k'),
      name: body.name.trim(),
      prefix: visible_prefix,
      scope: body.scope ?? 'full',
      last_used_at: null,
      created_at: new Date().toISOString(),
      revoked_at: null,
    };
    state.keys[session.account_id] = [...(state.keys[session.account_id] ?? []), key];
    state.fullKeys[key.id] = full;

    const created: ApiKeyCreated = { ...key, full_key: full };
    return HttpResponse.json<ApiKeyCreated>(created, { status: 201 });
  }),

  http.patch(`${BASE}/keys/:id`, async ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const id = String(params.id);
    const body = (await request.json()) as { name?: string };
    if (!body.name?.trim()) return validationError('Имя ключа обязательно');

    const list = state.keys[session.account_id] ?? [];
    const key = list.find((k) => k.id === id);
    if (!key) {
      return HttpResponse.json(
        { error: { type: 'not_found', message: 'Ключ не найден' } },
        { status: 404 },
      );
    }
    key.name = body.name.trim();
    return HttpResponse.json<ApiKey>(key);
  }),

  http.delete(`${BASE}/keys/:id`, ({ params, request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const id = String(params.id);
    const list = state.keys[session.account_id] ?? [];
    const key = list.find((k) => k.id === id);
    if (!key) {
      return HttpResponse.json(
        { error: { type: 'not_found', message: 'Ключ не найден' } },
        { status: 404 },
      );
    }
    key.revoked_at = new Date().toISOString();
    return new HttpResponse(null, { status: 204 });
  }),
];

// ============================================================
// Billing
// ============================================================

const billingHandlers = [
  http.get(`${BASE}/billing/balance`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const account = state.accounts[session.account_id];
    if (!account) return unauthorized();
    const balance: Balance = {
      balance_kopecks: account.balance_kopecks,
      autorefill_enabled: false,
    };
    return HttpResponse.json<Balance>(balance);
  }),

  http.post(`${BASE}/billing/topup`, async ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const body = (await request.json()) as { amount_rub?: number; return_url?: string };
    // Sprint 6: минимум 100 ₽ (см. TopupCard и 16_ceo_decisions.md).
    if (!body.amount_rub || body.amount_rub < 100) {
      return validationError('Минимум 100 ₽ за пополнение');
    }
    if (state.flags.forceTopupFail) {
      return HttpResponse.json(
        { error: { type: 'server_error', message: 'ЮKassa временно недоступна' } },
        { status: 502 },
      );
    }

    const payment_id = genId('pay');
    // В реальности мы редиректим на ЮKassa; в моках — сразу зачисляем баланс
    // и возвращаем фейковый URL, который mock-страница умеет показать.
    const account = state.accounts[session.account_id];
    if (account) {
      const kop = Math.round(body.amount_rub * 100) as Kopecks;
      account.balance_kopecks = (account.balance_kopecks + kop) as Kopecks;
      state.transactions[session.account_id]?.unshift({
        id: genId('t'),
        type: 'topup',
        amount_kopecks: kop,
        status: 'succeeded',
        description: `Пополнение через ЮKassa (моки)`,
        created_at: new Date().toISOString(),
        receipt_available: true,
      });
    }
    return HttpResponse.json(
      {
        payment_id,
        confirmation_url: `${body.return_url ?? '/app/billing'}?topup=success&amount=${body.amount_rub}`,
      },
      { status: 200 },
    );
  }),

  http.get(`${BASE}/billing/transactions`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get('limit') ?? '20');
    const offset = Number(url.searchParams.get('offset') ?? '0');
    // Sprint 8: search/filter params.
    const search = (url.searchParams.get('search') ?? '').trim().toLowerCase();
    const kinds = url.searchParams.getAll('kind');
    const fromIso = url.searchParams.get('from');
    const toIso = url.searchParams.get('to');
    const minAmount = url.searchParams.get('min_amount');
    const maxAmount = url.searchParams.get('max_amount');

    const fromMs = fromIso ? new Date(fromIso).getTime() : null;
    // `to` инклюзивный — расширяем до конца дня.
    const toMs = toIso ? new Date(toIso).getTime() + 86_399_999 : null;
    const minAbs = minAmount !== null ? Number(minAmount) : null;
    const maxAbs = maxAmount !== null ? Number(maxAmount) : null;

    const all = state.transactions[session.account_id] ?? [];
    const filtered = all.filter((t) => {
      if (kinds.length > 0 && !kinds.includes(t.type)) return false;
      if (search && !(t.description ?? '').toLowerCase().includes(search)) return false;
      if (fromMs !== null && new Date(t.created_at).getTime() < fromMs) return false;
      if (toMs !== null && new Date(t.created_at).getTime() > toMs) return false;
      const abs = Math.abs(t.amount_kopecks);
      if (minAbs !== null && abs < minAbs) return false;
      if (maxAbs !== null && abs > maxAbs) return false;
      return true;
    });
    const items = filtered.slice(offset, offset + limit);
    const page: TransactionsPage = {
      items,
      total: filtered.length,
      total_count: filtered.length,
      has_more: offset + limit < filtered.length,
    };
    return HttpResponse.json<TransactionsPage>(page);
  }),

  http.get(`${BASE}/billing/receipts/:transaction_id`, ({ request, params }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    // Возвращаем фейковый PDF blob — заголовок верный, тело — placeholder bytes.
    const body = new Uint8Array([
      0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a,
    ]);
    return new HttpResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="receipt-${String(params.transaction_id)}.pdf"`,
      },
    });
  }),

  http.post(`${BASE}/billing/autorefill`, async () => {
    return HttpResponse.json({ ok: true });
  }),
  http.delete(`${BASE}/billing/autorefill`, () => {
    return HttpResponse.json({ ok: true });
  }),
];

// ============================================================
// Usage
// ============================================================

const usageHandlers = [
  http.get(`${BASE}/usage`, ({ request }) => {
    const session = getSession(request);
    if (!session) return unauthorized();
    const url = new URL(request.url);
    const from = url.searchParams.get('from') ?? '';
    const to = url.searchParams.get('to') ?? '';
    const group_by = (url.searchParams.get('group_by') ?? 'day') as UsageGroupBy;

    const fromMs = from ? new Date(from).getTime() : 0;
    const toMs = to ? new Date(to).getTime() + 86_400_000 - 1 : Date.now() + 86_400_000;

    const records = (state.usage[session.account_id] ?? []).filter((r) => {
      if (!r.date) return true;
      const t = new Date(r.date).getTime();
      return t >= fromMs && t <= toMs;
    });

    let items: UsageItem[];
    if (group_by === 'day') {
      // Aggregate по date (все модели суммируются)
      const map = new Map<string, UsageItem>();
      for (const r of records) {
        const date = r.date ?? '';
        const cur =
          map.get(date) ??
          ({
            date,
            model: null,
            key_id: null,
            tokens_in: 0,
            tokens_out: 0,
            cached_tokens: 0,
            cost_kop: 0 as Kopecks,
          } satisfies UsageItem);
        cur.tokens_in += r.tokens_in;
        cur.tokens_out += r.tokens_out;
        cur.cached_tokens += r.cached_tokens;
        cur.cost_kop = (cur.cost_kop + r.cost_kop) as Kopecks;
        map.set(date, cur);
      }
      items = Array.from(map.values()).sort((a, b) =>
        (a.date ?? '').localeCompare(b.date ?? ''),
      );
    } else if (group_by === 'model') {
      const map = new Map<string, UsageItem>();
      for (const r of records) {
        const model = r.model ?? 'unknown';
        const cur =
          map.get(model) ??
          ({
            date: null,
            model,
            key_id: null,
            tokens_in: 0,
            tokens_out: 0,
            cached_tokens: 0,
            cost_kop: 0 as Kopecks,
          } satisfies UsageItem);
        cur.tokens_in += r.tokens_in;
        cur.tokens_out += r.tokens_out;
        cur.cached_tokens += r.cached_tokens;
        cur.cost_kop = (cur.cost_kop + r.cost_kop) as Kopecks;
        map.set(model, cur);
      }
      items = Array.from(map.values()).sort((a, b) => Number(b.cost_kop) - Number(a.cost_kop));
    } else {
      // group_by === 'key' — в фикстурах ключи не привязаны (key_id null), агрегируем в одну строку.
      const total: UsageItem = {
        date: null,
        model: null,
        key_id: null,
        tokens_in: records.reduce((s, r) => s + r.tokens_in, 0),
        tokens_out: records.reduce((s, r) => s + r.tokens_out, 0),
        cached_tokens: records.reduce((s, r) => s + r.cached_tokens, 0),
        cost_kop: records.reduce((s, r) => (s + r.cost_kop) as Kopecks, 0 as Kopecks),
      };
      items = [total];
    }

    const totals: UsageTotals = {
      tokens_in: records.reduce((s, r) => s + r.tokens_in, 0),
      tokens_out: records.reduce((s, r) => s + r.tokens_out, 0),
      cached_tokens: records.reduce((s, r) => s + r.cached_tokens, 0),
      cost_kop: records.reduce((s, r) => (s + r.cost_kop) as Kopecks, 0 as Kopecks),
      request_count: records.length, // в реальности — отдельное поле, в моках достаточно строк
    };

    const response: UsageResponse = { items, totals };
    return HttpResponse.json<UsageResponse>(response);
  }),
];

// ============================================================
// Демо: 500 на /v1/__force500 — чтобы проверять Sentry-flow.
// ============================================================

const debugHandlers = [
  http.get(`${BASE}/__force500`, () => {
    return HttpResponse.json(
      { error: { type: 'server_error', message: 'Simulated server error' } },
      { status: 500 },
    );
  }),
  http.get(`${BASE}/__force429`, () => tooManyRequests(2000)),
];

// ============================================================
// Playground (Sprint 12 — public sandbox без auth)
// ============================================================
//
// Endpoint: POST /v1/public/playground.
// В реальном backend — отдельный namespace с rate-limit middleware. Здесь
// возвращаем детерминированный mock, который позволяет E2E-тестам assert'ить
// success-flow без зависимости от LLM.
//
// Триггеры на ошибки в моке (для E2E):
//   prompt включает `[trigger:429]` → 429 + Retry-After
//   prompt включает `[trigger:503]` → 503
//   prompt включает `[trigger:5xx]` → 500
const playgroundHandlers = [
  http.post(`${BASE}/public/playground`, async ({ request }) => {
    const body = (await request.json()) as { model?: string; prompt?: string };
    const prompt = body.prompt ?? '';
    const model = body.model ?? 'deepseek-v4-flash';

    if (prompt.includes('[trigger:429]')) {
      return HttpResponse.json(
        { error: { type: 'rate_limit', message: 'Лимит 5 запросов/час' } },
        { status: 429, headers: { 'Retry-After': '600' } },
      );
    }
    if (prompt.includes('[trigger:503]')) {
      return HttpResponse.json(
        { error: { type: 'server_error', message: 'Sandbox over budget' } },
        { status: 503 },
      );
    }
    if (prompt.includes('[trigger:5xx]')) {
      return HttpResponse.json(
        { error: { type: 'server_error', message: 'LLM provider down' } },
        { status: 500 },
      );
    }

    await delay(300); // имитация латенси LLM, чтобы UI loading-state успел показаться
    return HttpResponse.json({
      id: 'chatcmpl-mock-playground',
      object: 'chat.completion',
      model,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: `(mock-ответ от ${model}) Получил промпт из ${prompt.length} символов. На реальном gateway здесь будет ответ модели.`,
          },
          finish_reason: 'stop',
        },
      ],
      usage: {
        prompt_tokens: Math.ceil(prompt.length / 4),
        completion_tokens: 25,
        total_tokens: Math.ceil(prompt.length / 4) + 25,
      },
    });
  }),
];

export const handlers = [
  ...authHandlers,
  ...accountHandlers,
  ...keysHandlers,
  ...billingHandlers,
  ...usageHandlers,
  ...playgroundHandlers,
  ...debugHandlers,
];

export const TEST_FIXTURES = { TEST_ACCOUNT_ID, TEST_USER_ID, TEST_SESSION_ID };
