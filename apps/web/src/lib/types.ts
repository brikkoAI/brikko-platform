/**
 * Доменные типы — соответствуют backend-контракту, утверждённому 29.04.
 * Поля сохраняются в snake_case как в API (так избегаем mapping-слоя в обе стороны).
 *
 * MVP — пишем вручную. В V1 переезжаем на openapi-typescript генерацию.
 */

// ============================================================
// Branded types — деньги храним в копейках, чтобы не словить
// ошибки округления float, типичные для подписочного биллинга.
// ============================================================

export type Kopecks = number & { readonly __brand: 'kopecks' };
export type RubFloat = number & { readonly __brand: 'rub_float' };

export const toKopecks = (n: number): Kopecks => n as Kopecks;
export const toRubFloat = (n: number): RubFloat => n as RubFloat;

/** Конверсии. Округление до целой копейки — банкирский rounding не нужен, т.к. MSW + biz inputs всегда integer-rub. */
export const kopecksToRub = (k: Kopecks): RubFloat => toRubFloat(k / 100);
export const rubToKopecks = (r: number): Kopecks => toKopecks(Math.round(r * 100));

// ============================================================
// Идентификаторы и время
// ============================================================

export type UUID = string;
export type ISODateString = string;

// ============================================================
// Auth
// ============================================================

export interface SignupResponse {
  user_id: UUID;
  email: string;
  verification_required: boolean;
  /**
   * DEV/staging only. Backend (api/auth.py:446) включает это поле когда SMTP не
   * настроен И app_env != production (или EXPOSE_DEV_VERIFY_URL=true). Полная
   * verify-URL вида `https://brikko.ru/signup/verify-email?token=…`.
   *
   * Production со включённым SMTP это поле НЕ возвращает — фронт скрывает
   * [DEV]-кнопку при отсутствии. Это критично: если поле всё-таки утечёт в prod
   * (например EXPOSE_DEV_VERIFY_URL=true по ошибке) — мы лучше покажем ссылку
   * чем дадим юзеру тыкать «битый» fake-link с email вместо token (баг 2026-05-02).
   */
  verify_url_dev?: string;
}

export interface LoginResponse {
  user_id: UUID;
  account_id: UUID;
  expires_at: ISODateString;
  /**
   * Свежий CSRF token. Backend ротирует его на каждый login/refresh — SPA
   * должен сохранить значение в memory-кэш через `setCsrfTokenFromResponse`.
   * См. docs/csrf_protocol.md §4.
   */
  csrf_token?: string;
}

export interface VerifyEmailResponse {
  verified: true;
  welcome_credit_kop: Kopecks;
}

// ============================================================
// Account
// ============================================================

export type Tariff =
  | 'payg'
  | 'pro'
  | 'pro_privacy'
  | 'team'
  | 'business'
  | 'business_privacy'
  | 'business_plus';

/**
 * Тарифы с включённым PII-маскингом (Sprint 4 V2 USP).
 * Используется для фронт-гейта в Settings → Privacy.
 * Источник: 04_Market/07_v2_monetization_research_2026-04-30.md §1.
 */
export const PII_ENABLED_TARIFFS: ReadonlySet<Tariff> = new Set([
  'pro_privacy',
  'business_privacy',
  'business_plus',
]);

export const isPiiTier = (t: Tariff): boolean => PII_ENABLED_TARIFFS.has(t);

/**
 * Telegram notification settings — boolean per category.
 * Backend (Поток M / Sprint 4) расширяет account.notifications JSON-blob этой формой.
 */
export interface TelegramNotificationSettings {
  balance_low?: boolean;
  failover_triggered?: boolean;
  key_created?: boolean;
}

export interface TelegramLink {
  /** Привязан ли Telegram. Если false — `chat_id` всегда null. */
  linked: boolean;
  /** Псевдо-ID бот-чата (показываем для подтверждения, не PII). */
  chat_id: string | null;
}

/**
 * Account snapshot.
 *
 * Backend (gateway/api/account.py:64) возвращает:
 *   {user_id, email, account_id, name, tariff, balance_kopecks,
 *    prompt_logging_enabled, notifications, created_at, email_verified}
 *
 * `prompt_logging_enabled` — API-имя; на DB-стороне это Account.store_prompts.
 * `notifications` — free-form blob (см. Account.settings.notifications).
 *
 * **Sprint 4:**
 *  - `pii_masking_enabled` — toggle PII-маскинга (см. Поток M backend: Account.pii_masking_enabled).
 *    Доступно только на Pro Privacy / Business Privacy / Business+ (см. PII_ENABLED_TARIFFS).
 *  - `telegram_link` — статус привязки TG-бота для алертов.
 */
export interface Account {
  user_id: UUID;
  email: string;
  account_id: UUID;
  name: string;
  tariff: Tariff;
  balance_kopecks: Kopecks;
  prompt_logging_enabled: boolean;
  notifications: Record<string, unknown>;
  created_at: ISODateString;
  email_verified: boolean;
  /**
   * Sprint 4 / Поток M. Optional на frontend — backend (legacy MVP-аккаунт без миграции)
   * может не возвращать поле; в этом случае трактуем как `false`.
   */
  pii_masking_enabled?: boolean;
  telegram_link?: TelegramLink;
  /**
   * Sprint 6: 2FA включена/нет. Backend (Поток N) расширяет /account.
   * Если поле отсутствует — UI трактует как `false` (legacy-аккаунты).
   */
  two_factor_enabled?: boolean;
  /** Sprint 6: дата активации 2FA — для отображения в Security tab. */
  two_factor_enabled_at?: ISODateString | null;
  /**
   * Sprint 6: запланировано ли закрытие аккаунта.
   * Если true — `scheduled_closure_at` обязателен, банер показывается на всех страницах.
   */
  closure_scheduled?: boolean;
  scheduled_closure_at?: ISODateString | null;
  /**
   * Sprint 7: расширенные поля account-closure.
   *
   * **Контракт (BE Sprint 6 уже выкатил, см. 16_ceo_decisions.md §closure_state):**
   *   - `closure_requested_at`  — когда юзер нажал «закрыть».
   *   - `closure_scheduled_at`  — когда аккаунт реально закроется (request + 30 дней).
   *   - `closure_reason`        — text-blob, optional.
   *   - `closed_at`             — момент финального soft-delete'а (после grace-period).
   *
   * Используются вместо legacy-полей `closure_scheduled` / `scheduled_closure_at`,
   * которые остаются для обратной совместимости с Sprint 6 UI'ём.
   *
   * TODO: regen from openapi after BE Sprint 7 merge.
   */
  closure_requested_at?: ISODateString | null;
  closure_scheduled_at?: ISODateString | null;
  closure_reason?: string | null;
  closed_at?: ISODateString | null;
}

export type SeatRole = 'owner' | 'admin' | 'member';

export interface Seat {
  user_id: UUID;
  email: string;
  role: SeatRole;
  joined_at: ISODateString;
}

export interface PendingInvite {
  invite_id: UUID;
  email: string;
  role: SeatRole;
  invited_at: ISODateString;
  expires_at: ISODateString;
}

// ============================================================
// API keys
// ============================================================

/**
 * Backend (gateway/api/keys.py:103) принимает scope как 'read'|'write'|'all'.
 * Frontend использует UX-friendly literals 'full'|'read_only'. Маппинг ниже.
 *
 * Backend list-response добавляет поле `status` ('active'|'revoked'). Мы сохраняем
 * derive-логику: revoked_at != null ⇒ revoked; иначе active. Оба варианта совместимы.
 */
export type ApiKeyScope = 'full' | 'read_only';
export type ApiKeyStatus = 'active' | 'revoked';

export interface ApiKey {
  id: UUID;
  name: string;
  prefix: string; // sk-vt-AB12 — последние 4 видимы, остальное замаскировано
  scope: ApiKeyScope;
  /** Backend: 'active'|'revoked'. MSW не возвращает (фронт деривирует из revoked_at). */
  status?: ApiKeyStatus;
  last_used_at: ISODateString | null;
  created_at: ISODateString;
  revoked_at: ISODateString | null;
}

/** Расширенный ответ при создании — full_key показываем РОВНО ОДИН раз. */
export interface ApiKeyCreated extends ApiKey {
  full_key: string;
}

// ============================================================
// MCP tokens (Sprint MCP S1)
//
// Brikko-MCP credentials — отдельный surface от ``sk-brk-*`` ключей.
// Подключаются в Claude Desktop / Cursor / Continue / Zed.
// Префикс плейнтекста: ``mcp-brk-...``. Backend: voltari_gateway.api.mcp_keys.
// ============================================================

// S3 (CEO 2026-05-12): four new read-only catalog scopes + ``all`` wildcard.
// ``all`` is the default for new tokens created via the helper-skill
// one-prompt onboarding. Restrictive tokens (specific scope) remain a
// valid choice for power users / agency tenants.
export type McpTokenScope =
  | 'read_account'
  | 'read_usage'
  | 'recommend_model'
  | 'list_models'
  | 'read_traces'
  | 'list_cookbook'
  | 'list_integrations'
  | 'all';
export type McpTokenStatus = 'active' | 'revoked';

export interface McpToken {
  id: UUID;
  name: string;
  prefix: string; // mcp-brk-aB12 — для распознавания в логах / clipboard
  scope: McpTokenScope;
  status: McpTokenStatus;
  last_used_at: ISODateString | null;
  created_at: ISODateString;
  revoked_at: ISODateString | null;
  expires_at: ISODateString | null;
}

/** Ответ при создании — full_token показываем ровно один раз. */
export interface McpTokenCreated {
  id: UUID;
  name: string;
  full_token: string;
  prefix: string;
  scope: McpTokenScope;
  created_at: ISODateString;
  expires_at: ISODateString | null;
}

// ============================================================
// Billing
// ============================================================

/**
 * Balance.
 *
 * Backend (gateway/api/billing.py:61) возвращает:
 *   {account_id, balance_kopecks, balance_rub, tariff}.
 * MSW (упрощённо): {balance_kopecks, autorefill_enabled}.
 *
 * Фронтенду balance_rub не нужен (имеем kopecksToRub в types.ts), а
 * autorefill_enabled читаем отдельным запросом — но MSW тестирует именно эту
 * форму, чтобы не тащить два source-of-truth. Обе формы совместимы:
 * `balance_kopecks` — общий ключ.
 */
export interface Balance {
  balance_kopecks: Kopecks;
  /** Есть только когда фронт сам собрал состояние (через /billing/autorefill GET). */
  autorefill_enabled?: boolean;
  /** Backend кладёт явно; MSW не возвращает. */
  account_id?: UUID;
  tariff?: Tariff;
}

/**
 * Transaction kind.
 *
 * Backend (gateway/db/models.TransactionKind) использует TOPUP/USAGE/REFUND/SUBSCRIPTION.
 * Welcome-credit маркируется как TOPUP с meta.kind="welcome" — отдельного типа на
 * backend нет (см. api/auth._try_grant_welcome_credit). Frontend различает welcome
 * через `meta.kind === 'welcome'`, но для UI'а помечаем отдельным типом 'welcome_credit'
 * (см. helper в TransactionsTable).
 */
export type TransactionType =
  | 'topup'
  | 'usage'
  | 'subscription'
  | 'refund'
  | 'welcome_credit';

export type TransactionStatus = 'pending' | 'succeeded' | 'failed';

/**
 * Транзакция.
 *
 * Backend (gateway/api/billing.py:68) поля: id, type, amount_kopecks, ref_id, created_at, meta.
 * MSW добавляет UI-friendly поля: status, description, receipt_available — фронт
 * деривирует их из meta для real backend (см. lib/transactions.ts).
 *
 * Гибридный контракт — оба варианта валидны.
 */
export interface Transaction {
  id: UUID;
  type: TransactionType;
  amount_kopecks: Kopecks;
  created_at: ISODateString;
  /** Backend: ref_id (payment_id ЮKassa, "welcome:<user_id>", и т.д.). */
  ref_id?: string | null;
  /** Backend: meta JSONB (kind, receipt, и т.д.). */
  meta?: Record<string, unknown> | null;
  /** Derived: успешная ли операция. MSW отдаёт явно, real backend → 'succeeded' если есть в БД. */
  status?: TransactionStatus;
  /** UI-text. Real backend: формируем из type + meta. */
  description?: string;
  /** Derived: есть ли чек (meta.receipt). */
  receipt_available?: boolean;
}

/**
 * Pagination format.
 *
 * Backend (gateway/api/billing.py:77) возвращает {items, next_cursor}.
 * MSW (упрощённо) возвращает {items, total}.
 *
 * Хелпер pagination в UI (TransactionsTable) принимает оба варианта.
 *
 * Sprint 8: добавили `total_count` (alias для `total`) и `has_more` — для
 * Prev/Next pagination в новой версии TransactionsTable.
 */
export interface TransactionsPage {
  items: Transaction[];
  /** MSW (Sprint <=7). */
  total?: number;
  /** Sprint 8: backend-canonical имя для total. */
  total_count?: number;
  /** Sprint 8: указывает, есть ли ещё страница вперёд. */
  has_more?: boolean;
  /** Backend cursor-based (legacy). */
  next_cursor?: string | null;
}

/**
 * Sprint 8: query params для поиска / фильтра.
 *
 * Backend Sprint 8 расширяет /v1/billing/transactions:
 *   - `search`     — поиск по description (case-insensitive substring).
 *   - `kind`       — повторяемый параметр: ?kind=topup&kind=usage (multi-select).
 *   - `min_amount` / `max_amount` — фильтр по абсолютной сумме в копейках.
 *
 * UX-нота: search debounce'ится на 300ms во фронте — не нагружаем backend
 * каждым нажатием клавиши.
 */
export interface TransactionsQuery {
  from?: string;
  to?: string;
  /** Возможные значения совпадают с TransactionType (без 'welcome_credit' — он рендерится из meta). */
  kind?: TransactionType[];
  search?: string;
  /** Абсолютные значения в копейках; знак (+/-) не различает. */
  min_amount?: Kopecks;
  max_amount?: Kopecks;
  limit?: number;
  offset?: number;
}

export interface TopupResponse {
  payment_id: UUID;
  confirmation_url: string;
  /** Backend возвращает дополнительно — фронт может проверить, что сумма не сабмитилась дважды. */
  amount_kopecks?: Kopecks;
}

export interface AutorefillSettings {
  card_id: string;
  threshold_kopecks: Kopecks;
  amount_kopecks: Kopecks;
}

// ============================================================
// Sprint 8: Auto-refill v2 (per-account, multi-card support)
//
// Спека: BRIEF Sprint 8 §1.
// API:
//   GET  /v1/account/autorefill  → AutorefillState
//   PUT  /v1/account/autorefill  → AutorefillState (full replace)
//   POST /v1/account/autorefill/disable → AutorefillState с enabled=false
//
// TODO: regen from openapi after BE Sprint 8 merge.
// ============================================================

/**
 * Сохранённый payment-method (карта) — backend возвращает после первого topup'а
 * через ЮKassa с saved_card опцией.
 */
export interface SavedPaymentMethod {
  /** ID, который backend использует при автосписании. */
  id: string;
  /** Маска вида «•••• 4242». */
  card_mask: string;
  /** «Visa» / «MasterCard» / «MIR» — для иконки. */
  brand: string;
  /** Дата сохранения — для UI «Привязана 12 мар 2026». */
  added_at: ISODateString;
  /** true для карты по умолчанию (на которую завязан текущий autorefill). */
  is_default: boolean;
}

/**
 * Ответ GET /v1/account/autorefill.
 *
 * Когда `enabled === false` — все остальные поля (кроме saved_methods) могут быть
 * null/0; UI трактует их как "не сконфигурировано".
 *
 * `failure_count` — сколько подряд авто-списаний не прошло (карта истекла, банк
 * отклонил). При `≥ 3` — backend сам disable'ит autorefill и шлёт алерт; UI
 * показывает банер «Обновить карту».
 */
export interface AutorefillState {
  enabled: boolean;
  threshold_kopecks: Kopecks | null;
  amount_kopecks: Kopecks | null;
  payment_method_id: string | null;
  saved_methods: SavedPaymentMethod[];
  /** Sprint 8 §1: 3 fail подряд → disable + банер. */
  failure_count: number;
  /** ISO-timestamp последнего failed-charge (для UI «последняя ошибка X дней назад»). */
  last_failure_at?: ISODateString | null;
  /** Текстовое описание последней ошибки от ЮKassa, если есть. */
  last_failure_reason?: string | null;
}

/**
 * Тело PUT /v1/account/autorefill.
 *
 * Validation на сервере:
 *   - `enabled === true` требует `threshold_kopecks > 0`, `amount_kopecks > 0` и
 *     `payment_method_id` из saved_methods.
 *   - `amount_kopecks ≥ 10000` (минимум 100 ₽ по правилу TopupCard).
 *   - `threshold_kopecks ≤ amount_kopecks` (иначе бесконечная петля списаний).
 */
export interface AutorefillUpdate {
  enabled: boolean;
  threshold_kopecks: Kopecks;
  amount_kopecks: Kopecks;
  payment_method_id: string;
}

// ============================================================
// Sprint 8: Activity feed (последние события аккаунта)
//
// Спека: BRIEF Sprint 8 §4. Backend Sprint 8 будет писать события
// в Account.activity_log при ключевых mutations. UI читает /v1/account/activity.
// ============================================================

/**
 * Тип события — закрытый union, чтобы маппинг иконок/копирайта был type-safe.
 *
 * Backend может прислать неизвестный тип (forward-compat); UI fallback'ит на
 * 'unknown' и показывает generic-иконку. Этот fallback в `iconForActivity`.
 */
export type ActivityEventType =
  | 'key_created'
  | 'key_revoked'
  | 'key_rotated'
  | 'balance_topup'
  | 'balance_low'
  | 'subscription_charged'
  | 'tariff_changed'
  | 'seat_invited'
  | 'seat_joined'
  | 'seat_removed'
  | 'login_new_device'
  | 'two_factor_enabled'
  | 'two_factor_disabled'
  | 'autorefill_charged'
  | 'autorefill_failed'
  | 'data_export_requested'
  | 'data_export_ready'
  | 'unknown';

export interface ActivityEvent {
  id: UUID;
  type: ActivityEventType;
  /** Главный текст в строке (первая строка, bold). */
  summary: string;
  /** Опциональные детали — рендерим в expand'е/modal'е. */
  details?: string | null;
  created_at: ISODateString;
  /** Дополнительные структурированные поля (amount_kopecks, key_name, etc). */
  meta?: Record<string, unknown> | null;
}

// ============================================================
// Sprint 8: X-Router-Decision header — поля в Usage events.
// Backend заполняет если запрос прошёл через smart-router auto:X.
// ============================================================

/**
 * Стратегия рутинга, выбравшая модель. Совпадает с RoutingStrategy
 * из routing-preferences (см. выше §Sprint 7) — но бывает null если
 * пользователь использовал manual-режим или явный model_tag.
 */
export type RoutingDecisionStrategy = 'cheap' | 'smart' | 'fast' | 'ru_legal' | 'custom';

export interface RoutingDecision {
  /** Стратегия, по которой роутер выбирал; null если manual/explicit. */
  strategy: RoutingDecisionStrategy | null;
  /** Фактически выбранная модель — может отличаться от запрошенной при auto:X. */
  model_chosen: string;
  /** Был ли использован failover (primary → fallback). */
  fallback_used: boolean;
}

// ============================================================
// Usage
// ============================================================

export type UsageGroupBy = 'day' | 'model' | 'key';

export interface UsageItem {
  date: ISODateString | null; // только для group_by=day
  model: string | null; // только для group_by=model
  key_id: UUID | null; // только для group_by=key
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_kop: Kopecks;
  /**
   * Sprint 8: routing decision полей (опциональные — старый backend не вернёт).
   * Заполняются только в group_by=model (или per-event endpoint), для day-aggregate
   * не имеет смысла усреднять стратегию.
   *
   * UX-нота: если все три undefined — UI просто не рендерит badges, не ломая layout.
   * См. лог `04_apps/web/src/components/dashboard/UsageRoutingBadges.tsx`.
   */
  routing_strategy?: RoutingDecisionStrategy | null;
  routing_model_chosen?: string | null;
  routing_fallback_used?: boolean;
}

export interface UsageTotals {
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_kop: Kopecks;
  request_count: number;
}

export interface UsageResponse {
  items: UsageItem[];
  totals: UsageTotals;
}

// ============================================================
// Errors — общий контракт
// ============================================================

export type ApiErrorType =
  | 'validation_error'
  | 'invalid_credentials'
  | 'email_already_registered'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'rate_limit'
  | 'insufficient_balance'
  | 'token_expired'
  | 'invite_already_member'
  | 'seat_limit_reached'
  | 'key_limit_reached'
  | 'mcp_token_limit_reached'
  | 'server_error'
  | 'network_error'
  | 'unknown_error';

export interface ApiErrorBody {
  error: {
    type: ApiErrorType;
    message: string;
    retry_after_ms?: number;
    details?: Record<string, unknown>;
    /**
     * Backend (FastAPI gateway) кладёт OpenAI-style error code (например
     * 'rate_limited', 'insufficient_balance'). MSW не использует — возвращает
     * только `type`. Маппинг см. lib/api.ts → mapErrorType.
     */
    code?: string;
    /** OpenAI-style error param: имя поля, провалившего валидацию. */
    param?: string;
  };
}

/** Унифицированный ответ form-сабмитов — дальше используем в формах. */
export type FormSubmitResult<T> =
  | { ok: true; data: T }
  | { ok: false; type: ApiErrorType; message: string; fieldErrors?: Record<string, string> };

// ============================================================
// Sprint 6: Tariff change, Security (2FA / sessions / closure), Data export
// ============================================================

/**
 * Тариф для запроса смены — backend принимает только литералы из этого union'а.
 * Не путать с `Tariff` из Account (там есть legacy 'pro' = pro_features).
 *
 * UX-нота: на frontend'е используем 'pro_features' как канон (читается легче,
 * чем 'pro'). Mapping в legacy `Tariff` — внутри handler'а PATCH /account/tariff.
 */
export type TariffSlug =
  | 'payg'
  | 'pro_features'
  | 'pro_privacy'
  | 'team'
  | 'business'
  | 'business_plus';

/** Ответ на PATCH /v1/account/tariff (контракт согласован Sprint 6). */
export interface TariffChangeResponse {
  tariff: TariffSlug;
  /** ISO-дата конца оплаченного периода. Для PAYG — null. */
  tariff_active_until: ISODateString | null;
  /**
   * Если true — на фронте после redirect'а на /settings показываем напоминалку
   * включить PII-маскинг (актуально для pro_privacy / business_privacy / business_plus).
   */
  requires_pii_setup: boolean;
}

/** Ответ POST /v1/auth/2fa/setup. */
export interface TwoFactorSetupResponse {
  secret: string; // base32-secret для manual ввода
  qr_code_url: string; // otpauth://… URL — фронт рендерит QR
  recovery_codes: string[]; // 8 одноразовых кодов
}

/** Активная сессия (GET /v1/auth/sessions). */
export interface AuthSession {
  id: UUID;
  /** Parsed-result User-Agent: «Chrome on Windows», «Safari on iOS». */
  device: string;
  ip: string;
  last_active_at: ISODateString;
  is_current: boolean;
}

/**
 * Запрос data-export (POST /v1/account/data-export).
 *
 * **Sprint 7:** добавляем `processing` и `failed` в enum статусов — backend Sprint 7
 * расширяет state-machine: `pending → processing → ready → expired` либо
 * `pending → failed`. Sprint 6-only клиенты, видящие новый статус, fallback'ятся
 * на «pending» (frontend проверяет `status in known_set`).
 *
 * TODO: regen from openapi after BE Sprint 7 merge.
 */
export type DataExportStatus = 'pending' | 'processing' | 'ready' | 'failed' | 'expired';

export interface DataExportRequest {
  id: UUID;
  status: DataExportStatus;
  requested_at: ISODateString;
  /** Если status === 'ready' — backend кладёт presigned URL. */
  download_url?: string | null;
  expires_at?: ISODateString | null;
  /** Sprint 7: ISO-timestamp финиша обработки (status → ready/failed/expired). */
  finished_at?: ISODateString | null;
  /** Sprint 7: human-readable текст ошибки (status === 'failed'). */
  error_message?: string | null;
}

/** Ответ POST /v1/account/close (запрос закрытия). */
export interface AccountClosureStatus {
  scheduled: boolean;
  /** Когда аккаунт реально закроется. null если scheduled === false. */
  scheduled_for: ISODateString | null;
  reason?: string | null;
}

// ============================================================
// Sprint 7: Routing preferences (per-account smart-router policy)
//
// Спека: 02_Product/v1.5/22_routing_preferences_spec.md
// TODO: regen from openapi after BE Sprint 7 merge.
// ============================================================

/** Manual = pinned-only model_tag; Smart = strategy-based router. */
export type RoutingMode = 'manual' | 'smart';

/**
 * 5 пресетов стратегии:
 *   - cheap     — минимальная цена (default для всех существующих аккаунтов)
 *   - smart     — баланс цены и качества
 *   - fast      — минимальная p50-латентность
 *   - ru_legal  — только RU-hosted провайдеры (152-ФЗ)
 *   - custom    — кастомный whitelist провайдеров и/или моделей
 */
export type RoutingStrategy = 'cheap' | 'smart' | 'fast' | 'ru_legal' | 'custom';

/**
 * 6 провайдеров (см. router/catalog.py).
 * Frontend хардкодит этот union — список редко меняется и backend всё равно
 * валидирует. Если catalog поменяется → пересборка фронта.
 */
export type RoutingProvider =
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'deepseek'
  | 'yandex'
  | 'sber';

export interface RoutingProviderInfo {
  id: RoutingProvider;
  label: string;
  /** Сколько моделей провайдер даёт активными в каталоге. */
  model_count: number;
}

/**
 * Preview-блок: что произойдёт с текущим filter'ом.
 * Backend считает estimated cost (см. §3.4 спеки) — frontend только рендерит.
 */
export interface RoutingPreview {
  /** Top-5 моделей при текущем filter'е. */
  active_models: string[];
  /** Сколько всего моделей пройдёт фильтр. */
  active_models_total: number;
  /**
   * Оценка средней цены в копейках за 1M токенов (weighted 70/30 input/output,
   * среднее по top-5 cheap'еr моделям). Backend кладёт сам.
   */
  estimated_avg_cost_kop_per_1m_tokens: number;
  /** Soft-warnings: «failover_disabled_single_provider», «no_models_for_code_category» и т.д. */
  warnings: string[];
}

/**
 * Полный snapshot текущих preferences + preview + список доступных провайдеров.
 *
 * GET /v1/account/routing-preferences возвращает именно это.
 */
export interface RoutingPreferences {
  routing_mode: RoutingMode;
  routing_strategy: RoutingStrategy;
  /** NULL = все провайдеры разрешены (legacy/non-custom). */
  allowed_providers: RoutingProvider[] | null;
  /** NULL = все модели провайдеров. */
  allowed_models: string[] | null;
  preview: RoutingPreview;
  /** Каталог провайдеров с лейблами и счётчиком моделей. */
  available_providers: RoutingProviderInfo[];
  /** Когда последний раз менялось — для audit-link UI. */
  updated_at: ISODateString | null;
}

/**
 * Тело PUT /v1/account/routing-preferences (полная замена).
 *
 * Validation на сервере (см. §3.3 спеки):
 *   - routing_mode === 'manual'  → strategy/allowed_* игнорируются.
 *   - routing_strategy === 'custom' → allowed_providers непустой массив.
 *   - routing_strategy !== 'custom' → allowed_providers/_models должны быть null.
 */
export interface RoutingPreferencesUpdate {
  routing_mode: RoutingMode;
  routing_strategy: RoutingStrategy;
  allowed_providers: RoutingProvider[] | null;
  allowed_models: string[] | null;
}
