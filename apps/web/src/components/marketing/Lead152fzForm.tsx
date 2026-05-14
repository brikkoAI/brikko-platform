'use client';

/**
 * Lead152fzForm — форма для лендинга /legal/152-fz (Sprint 14, 2026-05-07).
 *
 * Назначение:
 *   - конвертировать compliance-officer / CTO / юриста в подписчика, у которого
 *     мы запрашиваем минимум данных (email + роль), а взамен выдаём:
 *       (a) PDF-чеклист due-diligence по AI-vendor (1 стр) — ссылка после submit;
 *       (b) короткий e-mail-ответ под их кейс от founder'а в течение рабочего дня.
 *   - страховка через mailto-fallback: если backend ещё не выкатили или endpoint
 *     вернул 404 / сетевой fail — пользователь не теряет лид, он видит prefilled
 *     mailto-ссылку с теми же полями.
 *
 * UX-принципы:
 *   - максимум 4 поля. Compliance-офицер не будет тратить 2 минуты на форму.
 *   - email и role — обязательные. company / context — опциональные.
 *     Validation на client'е — мягкая, без агрессивных red-state до submit.
 *   - aria-live="polite" блок для статуса (ошибка / успех) — screen-reader
 *     анонсирует изменение, но не выхватывает фокус.
 *   - после успеха — не очищаем форму (вдруг хочет проверить что отправилось),
 *     показываем success-card + ссылку на PDF и mailto.
 *   - на mobile (375px) поля стекаются в один column, button full-width.
 *
 * Backend contract — см. docs/superpowers/specs/2026-05-07-leads-endpoint-spec.md.
 *   POST /v1/leads/152fz
 *   Body: { email, role, company?, context?, source_url, utm_* }
 *   200: { ok: true, checklist_url: string }
 *   404 / 5xx / network: показываем mailto-fallback.
 */

import { useState, useId, useMemo, type FormEvent } from 'react';

const COMPLIANCE_EMAIL = 'hello@brikko.ru';
const CHECKLIST_PATH = '/legal/152-fz-due-diligence-checklist.pdf';
const ENDPOINT = '/v1/leads/152fz';

const ROLES = [
  { value: '', label: '— выберите роль —' },
  { value: 'compliance', label: 'Compliance / DPO' },
  { value: 'cto', label: 'CTO / тех-руководитель' },
  { value: 'legal', label: 'Юрист / legal counsel' },
  { value: 'founder', label: 'Основатель / CEO' },
  { value: 'developer', label: 'Разработчик' },
  { value: 'other', label: 'Другое' },
] as const;

type Status = 'idle' | 'submitting' | 'success' | 'error';

interface FormData {
  email: string;
  role: string;
  company: string;
  context: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function buildMailto(data: FormData): string {
  const subject = '152-ФЗ compliance — запрос чеклиста';
  const lines = [
    `Email: ${data.email || '—'}`,
    `Роль: ${data.role || '—'}`,
    `Компания: ${data.company || '—'}`,
    '',
    'Описание кейса:',
    data.context || '—',
    '',
    '— отправлено через форму /legal/152-fz —',
  ];
  const body = lines.join('\n');
  return `mailto:${COMPLIANCE_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export function Lead152fzForm() {
  const formId = useId();
  const [data, setData] = useState<FormData>({
    email: '',
    role: '',
    company: '',
    context: '',
  });
  const [touched, setTouched] = useState<Record<keyof FormData, boolean>>({
    email: false,
    role: false,
    company: false,
    context: false,
  });
  const [status, setStatus] = useState<Status>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const errors = useMemo(() => {
    const e: Partial<Record<keyof FormData, string>> = {};
    if (!data.email.trim()) e.email = 'Укажите рабочий email — мы отправим чеклист и ответ туда.';
    else if (!EMAIL_RE.test(data.email.trim())) e.email = 'Похоже, в email опечатка.';
    if (!data.role) e.role = 'Выберите роль — это поможет ответить по делу.';
    return e;
  }, [data]);

  const isInvalid = (key: keyof FormData) => Boolean(touched[key] && errors[key]);

  function update<K extends keyof FormData>(key: K, value: FormData[K]) {
    setData((d) => ({ ...d, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched({ email: true, role: true, company: true, context: true });
    if (Object.keys(errors).length > 0) {
      setStatus('error');
      setErrorMessage('Проверьте подсвеченные поля и отправьте ещё раз.');
      return;
    }

    setStatus('submitting');
    setErrorMessage(null);

    // UTM + source — собираем на client'е, чтобы backend имел контекст откуда лид.
    const sourceUrl =
      typeof window !== 'undefined' ? window.location.href : '';
    const params =
      typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
    const utm = {
      utm_source: params?.get('utm_source') ?? null,
      utm_medium: params?.get('utm_medium') ?? null,
      utm_campaign: params?.get('utm_campaign') ?? null,
    };

    try {
      const response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: data.email.trim(),
          role: data.role,
          company: data.company.trim() || null,
          context: data.context.trim() || null,
          source_url: sourceUrl,
          ...utm,
        }),
      });

      if (response.ok) {
        setStatus('success');
        return;
      }

      // 404 (endpoint not deployed yet) / 5xx / 4xx — показываем fallback.
      throw new Error(`Server responded ${response.status}`);
    } catch (err) {
      // Network error / 404 / etc — даём mailto-fallback, не блокируем лид.
      setStatus('error');
      setErrorMessage(
        'Не удалось отправить форму автоматически. Это временно — пришлите тот же текст письмом, мы ответим в течение рабочего дня.',
      );
      // Не логируем err в console в production — это лендинг, а не dev-tool.
      // В dev — сообщение всё равно видно через Network tab.
    }
  }

  if (status === 'success') {
    return (
      <div
        className="brikko-card-flat"
        role="status"
        aria-live="polite"
        style={{ padding: 28 }}
      >
        <h3 className="text-xl font-semibold text-fg-primary">
          Готово. Чеклист и наш ответ — на пути к вам.
        </h3>
        <p className="mt-3 brikko-prose">
          Письмо с PDF-чеклистом и коротким ответом по вашему кейсу придёт на{' '}
          <strong className="text-fg-primary">{data.email}</strong> в течение рабочего дня.
          Если не пришло за 24 часа — проверьте папку «Спам» и напишите на{' '}
          <a href={`mailto:${COMPLIANCE_EMAIL}`} className="brikko-link">
            {COMPLIANCE_EMAIL}
          </a>
          .
        </p>
        <p className="mt-4 brikko-prose">
          Чеклист также можно скачать прямо сейчас:
        </p>
        <p className="mt-2">
          <a
            href={CHECKLIST_PATH}
            className="brikko-cta-primary"
            target="_blank"
            rel="noopener noreferrer"
            download
          >
            Скачать PDF (1 стр)
          </a>
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      aria-labelledby={`${formId}-title`}
      className="brikko-card-flat"
      style={{ padding: 28 }}
    >
      <h3 id={`${formId}-title`} className="text-xl font-semibold text-fg-primary">
        Получить чеклист и ответ под ваш кейс
      </h3>
      <p className="mt-2 text-body-sm text-fg-muted">
        4 поля. Спама не будет — пишем только по теме. Можно отписаться одной строкой.
      </p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <Field
          id={`${formId}-email`}
          label="Рабочий email"
          required
          invalid={isInvalid('email')}
          error={touched.email ? errors.email : undefined}
        >
          <input
            id={`${formId}-email`}
            name="email"
            type="email"
            autoComplete="email"
            inputMode="email"
            required
            value={data.email}
            onChange={(e) => update('email', e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, email: true }))}
            aria-invalid={isInvalid('email') || undefined}
            aria-describedby={isInvalid('email') ? `${formId}-email-error` : undefined}
            className={inputClass(isInvalid('email'))}
            placeholder="you@company.ru"
          />
        </Field>

        <Field
          id={`${formId}-role`}
          label="Ваша роль"
          required
          invalid={isInvalid('role')}
          error={touched.role ? errors.role : undefined}
        >
          <select
            id={`${formId}-role`}
            name="role"
            required
            value={data.role}
            onChange={(e) => update('role', e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, role: true }))}
            aria-invalid={isInvalid('role') || undefined}
            aria-describedby={isInvalid('role') ? `${formId}-role-error` : undefined}
            className={inputClass(isInvalid('role'))}
          >
            {ROLES.map((r) => (
              <option key={r.value} value={r.value} disabled={r.value === ''}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>

        <Field id={`${formId}-company`} label="Компания (необязательно)">
          <input
            id={`${formId}-company`}
            name="company"
            type="text"
            autoComplete="organization"
            value={data.company}
            onChange={(e) => update('company', e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, company: true }))}
            className={inputClass(false)}
            placeholder="Например, ООО «Ромашка»"
          />
        </Field>

        <Field
          id={`${formId}-context`}
          label="Кратко про кейс (необязательно)"
          spanFull
        >
          <textarea
            id={`${formId}-context`}
            name="context"
            value={data.context}
            onChange={(e) => update('context', e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, context: true }))}
            rows={3}
            className={`${inputClass(false)} h-auto py-2 leading-snug`}
            placeholder="Какие данные обрабатываете, какая модель LLM нужна, что блокирует."
            maxLength={1000}
          />
        </Field>
      </div>

      <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="submit"
          disabled={status === 'submitting'}
          aria-busy={status === 'submitting' || undefined}
          className="brikko-cta-primary disabled:opacity-60"
        >
          {status === 'submitting' ? 'Отправляем…' : 'Получить чеклист'}
        </button>
        <p className="text-body-sm text-fg-faint">
          Нажимая «Получить чеклист», вы соглашаетесь с{' '}
          <a href="/legal/privacy" className="brikko-link">
            политикой конфиденциальности
          </a>
          .
        </p>
      </div>

      {/* aria-live region — статус формы для screen-reader. Невидимо, если idle. */}
      <div role="alert" aria-live="polite" className="mt-4">
        {status === 'error' && errorMessage ? (
          <div
            className="rounded-md border border-error-200 bg-error-50 px-4 py-3 text-body-sm"
            style={{ color: 'var(--fg-primary)' }}
          >
            <p>{errorMessage}</p>
            <p className="mt-2">
              Кнопка-страховка:{' '}
              <a href={buildMailto(data)} className="brikko-link">
                написать письмом на {COMPLIANCE_EMAIL}
              </a>
              .
            </p>
          </div>
        ) : null}
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  required,
  invalid,
  error,
  spanFull,
  children,
}: {
  id: string;
  label: string;
  required?: boolean;
  invalid?: boolean;
  error?: string;
  spanFull?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={spanFull ? 'sm:col-span-2' : undefined}>
      <label
        htmlFor={id}
        className="block text-body-sm font-medium text-fg-primary"
      >
        {label}
        {required ? (
          <span className="ml-1 text-error-600" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <div className="mt-1.5">{children}</div>
      {invalid && error ? (
        <p
          id={`${id}-error`}
          className="mt-1.5 text-body-sm"
          style={{ color: 'var(--fg-primary)' }}
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}

function inputClass(invalid: boolean): string {
  const base =
    'w-full rounded-md border bg-bg-base px-3 h-10 text-body text-fg-primary placeholder:text-fg-faint transition-colors focus-visible:outline-none focus-visible:ring-2';
  return invalid
    ? `${base} border-error-600 focus-visible:ring-error-600/20`
    : `${base} border-[var(--hairline-hi)] hover:border-[var(--fg-faint)] focus-visible:border-[var(--fg-primary)] focus-visible:ring-[var(--fg-primary)]/15`;
}
