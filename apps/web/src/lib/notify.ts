/**
 * Frontend mirror to TG-bot alerts (см. 11_dashboard_states_and_flows.md §6.1).
 *
 * Зачем:
 *   - Юзер не всегда привязал TG (см. §1.6). Когда он залогинен — тосты в dashboard
 *     дают ему то же содержание, что прилетает в @VoltariBot.
 *   - Тексты в точности из R-vetted §6.1, чтобы не было расхождения email ⇄ TG ⇄ UI.
 *
 * UX-обоснование:
 *   - В копирайт-гайде §0 — без эмодзи, без восклицаний, точка в конце.
 *   - balance.zero / suspicious / payment.failure — error-вариант (красный),
 *     payment.success / key.created — success (зелёный),
 *     balance.low / rate.limit — warning (жёлтый).
 *   - Жёсткие N-bsp в числах (1 000 ₽) — формат уже на стороне formatKopecks.
 */
import { toast } from '@/components/ui/toast';
import { formatKopecks } from '@/lib/utils';
import type { Kopecks } from '@/lib/types';

const NBSP_RUR = (kop: Kopecks | number) =>
  formatKopecks(kop as Kopecks, { forceFraction: false });

/** Balance below 10% of avg monthly. Источник: §6.1.1. */
export function notifyBalanceLow(args: {
  balance_kop: Kopecks;
  avg_monthly_kop: Kopecks;
}) {
  return toast.warning(
    `Баланс ${NBSP_RUR(args.balance_kop)} — это ~10 % от среднего месячного расхода (${NBSP_RUR(args.avg_monthly_kop)}).`,
    { description: 'Пополнить, чтобы запросы не остановились.' },
  );
}

/** Balance critical (< 3 %). §6.1.2. */
export function notifyBalanceCritical(args: { balance_kop: Kopecks }) {
  return toast.warning(
    `Баланс ${NBSP_RUR(args.balance_kop)}. Запросы остановятся в течение часа при текущем темпе.`,
    { description: 'Пополнить сейчас.' },
  );
}

/** Balance zero — запросы 402. §6.1.3. */
export function notifyBalanceZero() {
  return toast.error('Баланс 0 ₽. Все запросы возвращают 402.', {
    description: 'Пополнить, чтобы возобновить работу.',
  });
}

/** Payment success — receipt отправлен. §6.1.4. */
export function notifyPaymentSuccess(args: {
  amount_kop: Kopecks;
  balance_kop: Kopecks;
  email: string;
}) {
  return toast.success(
    `Платёж ${NBSP_RUR(args.amount_kop)} зачислен. Текущий баланс: ${NBSP_RUR(args.balance_kop)}.`,
    { description: `Чек отправлен на ${args.email}.` },
  );
}

/** New API key created. §6.1.5. */
export function notifyKeyCreated(args: { name: string; prefix: string }) {
  return toast.success(`Создан новый API-ключ: ${args.name} (${args.prefix}…).`, {
    description: 'Если это сделал не ты — отозвать в настройках сейчас.',
  });
}

/** Suspicious activity — login from new IP. §6.1.6. */
export function notifySuspiciousLogin(args: { ip: string; city?: string; country?: string }) {
  const where = [args.city, args.country].filter(Boolean).join(', ');
  return toast.warning(
    `Вход в аккаунт с нового IP ${args.ip}${where ? ` (${where})` : ''}.`,
    { description: 'Если это не ты — сменить пароль и отозвать активные сессии.' },
  );
}

/** Rate limit hit > 10 раз за день. §6.1.7. */
export function notifyRateLimit(args: { hits: number; tariff_label: string; rpm: number }) {
  return toast.warning(
    `За сегодня сработал rate-limit ${args.hits} раз. Лимит на тарифе ${args.tariff_label} — ${args.rpm} запросов в минуту.`,
    { description: 'Поднять лимит — перейти на Team или выше.' },
  );
}
