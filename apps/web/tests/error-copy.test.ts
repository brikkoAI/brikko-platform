/**
 * error-copy.ts — снапшот-тест R-vetted формулировок.
 *
 * Если кто-то решит «улучшить» формулировку без апдейта PM-документа —
 * этот тест зафиксирует расхождение. Тексты дословно из
 * 02_Product/v1.5/11_dashboard_states_and_flows.md §5.
 */
import { describe, it, expect } from 'vitest';
import {
  EMAIL_VERIFY_EXPIRED,
  EMAIL_VERIFY_USED,
  INSUFFICIENT_BALANCE,
  PAYMENT_FAILURE_DETAIL,
  PAYMENT_FAILURE_DESCRIPTION,
  PAYMENT_FAILURE_TITLE,
  PII_MASKING_FAILED,
  RATE_LIMIT,
  TG_TOKEN_EXPIRED,
  TG_TOKEN_USED,
} from '@/lib/error-copy';

describe('error-copy: R-vetted contracts', () => {
  it('§5.9 payment failure — generic title и описание', () => {
    expect(PAYMENT_FAILURE_TITLE).toBe('Платёж не прошёл');
    expect(PAYMENT_FAILURE_DESCRIPTION).toBe(
      'Деньги не списаны. Попробовать другой способ оплаты или другую карту.',
    );
  });

  it('§5.9 payment failure detail: каждый reason из доки покрыт', () => {
    expect(PAYMENT_FAILURE_DETAIL.insufficient_funds).toMatch(/недостаточно средств/);
    expect(PAYMENT_FAILURE_DETAIL.card_declined).toMatch(/Банк отклонил/);
    expect(PAYMENT_FAILURE_DETAIL['3ds_failed']).toMatch(/3-D Secure/);
    expect(PAYMENT_FAILURE_DETAIL.card_expired).toMatch(/Срок действия карты/);
    expect(PAYMENT_FAILURE_DETAIL.network_error).toMatch(/Связь с банком прервалась/);
    expect(PAYMENT_FAILURE_DETAIL.unknown).toMatch(/Платёж отклонён без указания причины/);
  });

  it('§5.6 TG token expired — primary CTA «Создать новую ссылку»', () => {
    expect(TG_TOKEN_EXPIRED.title).toBe('Срок действия ссылки истёк');
    expect(TG_TOKEN_EXPIRED.primaryCta).toBe('Создать новую ссылку');
  });

  it('§5.6 TG token used — primary CTA «Открыть настройки»', () => {
    expect(TG_TOKEN_USED.title).toBe('Ссылка уже использована');
    expect(TG_TOKEN_USED.primaryCta).toBe('Открыть настройки');
  });

  it('§5.7 email verify expired — TTL 24 часа упомянут', () => {
    expect(EMAIL_VERIFY_EXPIRED.description).toMatch(/24 часа/);
    expect(EMAIL_VERIFY_EXPIRED.primaryCta).toBe('Отправить новое письмо');
  });

  it('§5.8 email verify used — success-tone «Аккаунт активен»', () => {
    expect(EMAIL_VERIFY_USED.title).toBe('Почта уже подтверждена');
    expect(EMAIL_VERIFY_USED.primaryCta).toBe('Войти');
  });

  it('§5.1 insufficient balance — buildTitle подставляет formatted balance', () => {
    expect(INSUFFICIENT_BALANCE.buildTitle('12 ₽')).toBe('На балансе 12 ₽ — запросы остановлены');
    expect(INSUFFICIENT_BALANCE.description).toBe(
      'Пополнить баланс, чтобы продолжить. Минимальное пополнение — 100 ₽.',
    );
  });

  it('§5.4 rate-limit — buildTitle подставляет hits count', () => {
    expect(RATE_LIMIT.buildTitle(14)).toBe('Превышены лимиты — 14 раз сегодня');
    expect(RATE_LIMIT.description).toMatch(/60 запросов в минуту/);
    expect(RATE_LIMIT.primaryCta).toBe('Сравнить тарифы');
  });

  it('§5.5 pii masking failed — buildDescription включает request_id', () => {
    expect(PII_MASKING_FAILED.title).toBe('Ошибка маскирования ПДн');
    expect(PII_MASKING_FAILED.buildDescription('req_abc')).toMatch(/req_abc/);
    expect(PII_MASKING_FAILED.buildDescription('req_abc')).toMatch(/Не удалось обработать ПДн/);
  });
});
