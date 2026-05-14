import type { Transaction, TransactionStatus } from './types';

/**
 * Real backend (gateway/api/billing.py:68) возвращает поля:
 *   {id, type, amount_kopecks, ref_id, created_at, meta}
 * MSW добавляет UI-friendly: {status, description, receipt_available}.
 *
 * Эти helper'ы деривируют недостающие UI-поля из backend-shape — UI-компоненты
 * вызывают одинаковые `getDescription/getStatus/getReceiptAvailable` независимо
 * от источника, что упрощает и тестирование, и миграцию.
 */

const TYPE_LABELS: Record<Transaction['type'], string> = {
  topup: 'Пополнение',
  usage: 'Списание за использование',
  subscription: 'Подписка',
  refund: 'Возврат',
  welcome_credit: 'Welcome-бонус',
};

export function getDescription(t: Transaction): string {
  if (t.description) return t.description;
  // Backend marks welcome via meta.kind = "welcome" — derive отдельный label.
  if (t.type === 'topup' && (t.meta as { kind?: string } | null)?.kind === 'welcome') {
    return 'Welcome-бонус 200 ₽';
  }
  if (t.type === 'topup' && t.ref_id?.startsWith('welcome:')) {
    return 'Welcome-бонус 200 ₽';
  }
  return TYPE_LABELS[t.type];
}

export function getStatus(t: Transaction): TransactionStatus {
  if (t.status) return t.status;
  // Real backend хранит только успешно проведённые транзакции (pending хранится в YooKassa, не у нас).
  return 'succeeded';
}

export function getReceiptAvailable(t: Transaction): boolean {
  if (typeof t.receipt_available === 'boolean') return t.receipt_available;
  const meta = (t.meta ?? {}) as { receipt?: { url?: string; id?: string } };
  return Boolean(meta.receipt?.url || meta.receipt?.id);
}
