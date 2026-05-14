/**
 * Error-state copy registry — единый источник для всех frontend-ошибок.
 * Тексты дословно из 02_Product/v1.5/11_dashboard_states_and_flows.md §5.
 *
 * Зачем такой модуль:
 *   - Тексты ошибок встречаются в 4+ местах (banner / toast / page / dialog), и в каждом
 *     надо подставлять одинаково. Если разьехались — нарушение контракта PM ↔ FE.
 *   - Все строки — R-vetted; правки только через PM (ССК §9.2).
 */

export type PaymentFailureReason =
  | 'insufficient_funds'
  | 'card_declined'
  | '3ds_failed'
  | 'card_expired'
  | 'network_error'
  | 'unknown';

/** §5.9 — детали по reason-code. */
export const PAYMENT_FAILURE_DETAIL: Record<PaymentFailureReason, string> = {
  insufficient_funds: 'На карте недостаточно средств. Проверить баланс или выбрать другую карту.',
  card_declined: 'Банк отклонил операцию. Проверить с банком или попробовать СБП.',
  '3ds_failed': 'Не пройдено подтверждение 3-D Secure. Повторить и подтвердить смс из банка.',
  card_expired: 'Срок действия карты истёк. Использовать другую карту.',
  network_error: 'Связь с банком прервалась. Деньги не списаны. Повторить через минуту.',
  unknown: 'Платёж отклонён без указания причины. Попробовать СБП или связаться с поддержкой.',
};

/** §5.9 generic. */
export const PAYMENT_FAILURE_TITLE = 'Платёж не прошёл';
export const PAYMENT_FAILURE_DESCRIPTION =
  'Деньги не списаны. Попробовать другой способ оплаты или другую карту.';

/** §5.6 — Telegram link token expired. */
export const TG_TOKEN_EXPIRED = {
  title: 'Срок действия ссылки истёк',
  description: 'Ссылка для привязки Telegram действует 5 минут. Создать новую и подтвердить сразу.',
  primaryCta: 'Создать новую ссылку',
  secondaryCta: 'Вернуться в настройки',
} as const;

/** §5.6 — Telegram link token already used. */
export const TG_TOKEN_USED = {
  title: 'Ссылка уже использована',
  description:
    'Telegram-аккаунт уже привязан. Если это сделал не ты — отвязать в настройках и обратиться в поддержку.',
  primaryCta: 'Открыть настройки',
  secondaryCta: 'Связаться с поддержкой',
} as const;

/** §5.7 — Email verify token expired (TTL 24 ч). */
export const EMAIL_VERIFY_EXPIRED = {
  title: 'Срок действия ссылки истёк',
  description: 'Ссылка действовала 24 часа. Запросить новое письмо — и подтвердить почту в течение суток.',
  primaryCta: 'Отправить новое письмо',
  secondaryCta: 'Войти под другим email',
} as const;

/** §5.8 — Email verify token already used (нормальная ситуация, success-вариант). */
export const EMAIL_VERIFY_USED = {
  title: 'Почта уже подтверждена',
  description: 'Аккаунт активен. Войти и продолжить работу.',
  primaryCta: 'Войти',
} as const;

/** §5.1 — Insufficient balance banner для всех /app/*. */
export const INSUFFICIENT_BALANCE = {
  // Title подставляет {balance} — kopecks → ₽ format в caller'е.
  buildTitle: (balanceFormatted: string) => `На балансе ${balanceFormatted} — запросы остановлены`,
  description: 'Пополнить баланс, чтобы продолжить. Минимальное пополнение — 100 ₽.',
  primaryCta: 'Пополнить',
} as const;

/** §5.4 — Rate limit hit banner (если > 10 раз за день). */
export const RATE_LIMIT = {
  buildTitle: (hitsToday: number) => `Превышены лимиты — ${hitsToday} раз сегодня`,
  description: 'На текущем тарифе лимит — 60 запросов в минуту. Поднять лимит: перейти на Team или выше.',
  primaryCta: 'Сравнить тарифы',
} as const;

/** §5.5 — PII masking failed (rare; обычно невидимо для юзера). */
export const PII_MASKING_FAILED = {
  title: 'Ошибка маскирования ПДн',
  buildDescription: (requestId: string) =>
    `Не удалось обработать ПДн в запросе. Запрос не отправлен в провайдера для безопасности. Обратиться в поддержку с request_id: ${requestId}.`,
} as const;
