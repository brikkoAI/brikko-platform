'use client';

import Link from 'next/link';
import { ArrowRight, Copy, Check, Loader2, Play, Sparkles } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Banner } from '@/components/ui/banner';
import { toast } from '@/components/ui/toast';
import { track } from '@/lib/analytics';

/**
 * PlaygroundForm — Cream Studio v6.
 *
 * UX-обоснование решений (без изменений):
 *   - Двухколоночный layout на desktop: форма слева, ответ справа. Юзер видит
 *     результат, не теряя контекст ввода — проще итерировать промпт.
 *   - 4 chip'а с готовыми примерами — снимают «страх пустого поля».
 *   - GPT-5.4 mini auto-selected — стабильно отдаёт content при коротком
 *     max_tokens=150 sandbox-budget'а.
 *   - Counter X/500 у textarea — стандарт UX (Twitter-pattern).
 *   - Disclaimer про PII прямо у формы — compliance-требование 152-ФЗ.
 *   - Conversion modal после 3-го успеха — non-blocking.
 *
 * Visual: Cream Studio v6 — espresso primary CTA, hairline-border на input/select,
 * terminal-стиль bg-tier-2 + Geist Mono для response-area. В dark theme code-shell
 * инвертируется автоматом (cream bg + espresso fg).
 */

interface ModelOption {
  id: string;
  label: string;
  hint?: string;
}

const MODELS: readonly ModelOption[] = [
  { id: 'gpt-5.4-mini', label: 'GPT-5.4 mini', hint: 'быстрая, универсальная' },
  { id: 'claude-haiku-4.5', label: 'Claude Haiku 4.5' },
  { id: 'gemini-3-flash', label: 'Gemini 3 Flash' },
  { id: 'yandexgpt-5-lite', label: 'YandexGPT 5 Lite' },
  { id: 'gigachat-2-lite', label: 'GigaChat 2 Lite' },
];

const DEFAULT_MODEL_ID = 'gpt-5.4-mini';

interface QuickExample {
  label: string;
  prompt: string;
}

const QUICK_EXAMPLES: readonly QuickExample[] = [
  {
    label: 'Классификация лида',
    prompt:
      'Классифицируй обращение как «горячий лид», «холодный» или «спам». Текст: «Здравствуйте! Хотим обсудить внедрение AI в наш отдел продаж, бюджет 500 тыс/мес, нужно созвониться на этой неделе.» Ответь одним словом + одна строка обоснования.',
  },
  {
    label: 'Резюме звонка',
    prompt:
      'Сделай краткое резюме звонка в 3 пунктах: ключевая договорённость, action items, риски. Транскрипт: «Клиент хочет внедрить чат-бота к 15 июня, бюджет до 300 тыс, согласовали ТЗ на следующей неделе, опасается за интеграцию с 1С.»',
  },
  {
    label: 'Перевод RU→EN',
    prompt:
      'Переведи на английский деловым тоном: «Здравствуйте, отправляем коммерческое предложение на интеграцию AI-помощника. Готовы обсудить детали на следующей неделе.»',
  },
  {
    label: 'Извлечь ключевые факты',
    prompt:
      'Извлеки ключевые факты в JSON {company, budget, deadline, contact}. Текст: «Звонил Иван из ООО Ромашка, бюджет до 500 тыс, нужно к 1 июля, телефон +79991234567.»',
  },
] as const;

const MAX_PROMPT_LENGTH = 500;
const SUCCESS_MODAL_THRESHOLD = 3;

type LoadingState = { kind: 'idle' } | { kind: 'loading' } | { kind: 'success' } | { kind: 'error' };

interface SuccessResult {
  text: string;
  model: string;
  latencyMs: number;
  tokens: number;
}

interface ApiError {
  status: number;
  retryAfterSec?: number;
  message: string;
}

function resolveApiBaseUrl(): string {
  const env = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!env || /localhost:3000/.test(env)) return '/api/mock/v1';
  return env.replace(/\/$/, '');
}

interface PlaygroundResponseBody {
  choices?: Array<{ message?: { content?: string } }>;
  usage?: { total_tokens?: number };
  model?: string;
}

export function PlaygroundForm() {
  const [model, setModel] = useState<string>(DEFAULT_MODEL_ID);
  const [prompt, setPrompt] = useState<string>('');
  const [state, setState] = useState<LoadingState>({ kind: 'idle' });
  const [result, setResult] = useState<SuccessResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [successCount, setSuccessCount] = useState<number>(0);
  const [copied, setCopied] = useState<boolean>(false);
  const [showSignupModal, setShowSignupModal] = useState<boolean>(false);

  const promptId = useId();
  const counterId = useId();
  const formRef = useRef<HTMLFormElement>(null);

  const charCount = prompt.trim().length;
  const canSubmit =
    charCount > 0 && charCount <= MAX_PROMPT_LENGTH && state.kind !== 'loading';

  const placeholder = useMemo(
    () =>
      'Классифицируй: эта почта — спам или нет? Текст ниже...\n\nИли выбери готовый пример под полем.',
    [],
  );

  function pickExample(example: QuickExample) {
    setPrompt(example.prompt);
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!canSubmit) return;

    setState({ kind: 'loading' });
    setError(null);
    const startedAt = Date.now();

    try {
      const res = await fetch(`${resolveApiBaseUrl()}/public/playground`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ model, prompt: prompt.trim() }),
      });

      if (!res.ok) {
        const retryHeader = res.headers.get('retry-after');
        const retryAfterSec = retryHeader ? Number(retryHeader) : undefined;
        const apiError: ApiError = {
          status: res.status,
          retryAfterSec: Number.isFinite(retryAfterSec) ? retryAfterSec : undefined,
          message: '',
        };
        if (res.status === 429) {
          track('playground_limit_hit', { model });
          apiError.message =
            'Лимит 5 запросов в час исчерпан. Подождать или зарегистрироваться сейчас.';
        } else if (res.status === 503) {
          apiError.message =
            'Sandbox временно недоступен. Зарегистрируйся для собственного баланса.';
        } else {
          apiError.message = 'Что-то пошло не так. Попробуй другую модель.';
        }
        setError(apiError);
        setState({ kind: 'error' });
        return;
      }

      const body = (await res.json()) as PlaygroundResponseBody;
      const text =
        body.choices?.[0]?.message?.content?.toString().trim() ||
        'Модель вернула пустой ответ. Попробуй другую модель или переформулируй промпт.';
      const latencyMs = Date.now() - startedAt;
      const tokens = body.usage?.total_tokens ?? 0;

      const next: SuccessResult = {
        text,
        model: body.model ?? model,
        latencyMs,
        tokens,
      };
      setResult(next);
      setState({ kind: 'success' });
      setSuccessCount((c) => {
        const newCount = c + 1;
        track('playground_used', { model, success_count: newCount });
        if (newCount === SUCCESS_MODAL_THRESHOLD) {
          setShowSignupModal(true);
        }
        return newCount;
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Сеть недоступна.';
      setError({ status: 0, message: `Сетевая ошибка: ${message}` });
      setState({ kind: 'error' });
    }
  }

  async function copyAnswer() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.text);
      setCopied(true);
      toast.success('Ответ скопирован');
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('Не удалось скопировать. Скопируй вручную.');
    }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr]">
      <section aria-labelledby="playground-heading">
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Playground
        </p>
        <h1 id="playground-heading" className="brikko-h1 mt-3">
          Попробуй прямо сейчас
        </h1>
        <p className="brikko-lede mt-4">
          5 запросов в час бесплатно. Хочешь больше — зарегистрируйся за 5 минут с
          200 ₽ welcome-бонусом.
        </p>

        <form
          ref={formRef}
          onSubmit={handleSubmit}
          className="mt-8 flex flex-col gap-5"
          noValidate
        >
          <div className="flex flex-col gap-2">
            <label
              htmlFor="playground-model"
              className="text-body-sm font-medium text-fg-primary"
            >
              Модель
            </label>
            <select
              id="playground-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="brikko-select"
              data-testid="playground-model"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.hint ? ` — ${m.hint}` : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-2">
            <label
              htmlFor={promptId}
              className="text-body-sm font-medium text-fg-primary"
            >
              Запрос
            </label>
            <textarea
              id={promptId}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value.slice(0, MAX_PROMPT_LENGTH))}
              placeholder={placeholder}
              rows={6}
              maxLength={MAX_PROMPT_LENGTH}
              aria-describedby={counterId}
              className="brikko-textarea"
              data-testid="playground-prompt"
            />
            <div
              id={counterId}
              className={`text-right text-body-sm tabular-nums ${
                charCount >= MAX_PROMPT_LENGTH ? 'text-error-600' : 'text-fg-faint'
              }`}
              aria-live="polite"
            >
              {charCount}/{MAX_PROMPT_LENGTH}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <p className="text-body-sm font-medium text-fg-muted">Готовые примеры</p>
            <div className="flex flex-wrap gap-2">
              {QUICK_EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  type="button"
                  onClick={() => pickExample(ex)}
                  className="brikko-chip"
                  data-testid={`playground-example-${ex.label}`}
                >
                  <Sparkles
                    className="h-3.5 w-3.5"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                  {ex.label}
                </button>
              ))}
            </div>
          </div>

          <button
            type="submit"
            disabled={!canSubmit}
            aria-disabled={!canSubmit}
            className="brikko-cta-primary w-fit"
            data-testid="playground-submit"
          >
            {state.kind === 'loading' ? (
              <>
                <Loader2
                  className="h-4 w-4 animate-spin"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
                <span>Запрос идёт</span>
              </>
            ) : (
              <>
                <Play className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                <span>Запустить</span>
              </>
            )}
          </button>

          <p className="text-body-sm text-fg-faint">
            Не вписывай реальные персональные данные — всё что введёшь, мы маскируем
            перед отправкой в LLM, но playground публичный.
          </p>
        </form>
      </section>

      <section
        aria-labelledby="playground-output"
        className="brikko-card-bezel flex min-h-[420px] flex-col"
      >
        <div className="brikko-card-bezel-inner flex flex-1 flex-col">
          <h2
            id="playground-output"
            className="text-lg font-semibold text-fg-primary"
          >
            Ответ модели
          </h2>

          {state.kind === 'idle' ? (
            <div className="flex flex-1 items-center justify-center">
              <p className="max-w-xs text-center text-body-sm text-fg-faint">
                Выбери модель, введи запрос или нажми на готовый пример — ответ
                появится здесь через ~3 секунды.
              </p>
            </div>
          ) : null}

          {state.kind === 'loading' ? (
            <div
              className="flex flex-1 flex-col items-center justify-center gap-3 text-fg-muted"
              role="status"
              aria-live="polite"
            >
              <Loader2
                className="h-8 w-8 animate-spin text-fg-primary"
                strokeWidth={1.5}
                aria-hidden="true"
              />
              <p className="text-body-sm">
                Ждём ответ от <span className="font-mono">{model}</span>…
              </p>
            </div>
          ) : null}

          {state.kind === 'error' && error ? (
            <PlaygroundErrorView
              error={error}
              onRetry={() => formRef.current?.requestSubmit()}
              onSignupClick={() =>
                track('playground_signup_cta_clicked', {
                  from: 'error',
                  status: error.status,
                })
              }
            />
          ) : null}

          {state.kind === 'success' && result ? (
            <div className="mt-4 flex flex-1 flex-col gap-4">
              <pre className="brikko-terminal flex-1" data-testid="playground-result">
                {result.text}
              </pre>
              <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body-sm text-fg-faint">
                <div className="flex items-baseline gap-1">
                  <dt>модель:</dt>
                  <dd className="font-mono text-fg-muted">{result.model}</dd>
                </div>
                <div aria-hidden="true">|</div>
                <div className="flex items-baseline gap-1">
                  <dt>время:</dt>
                  <dd className="tabular-nums text-fg-muted">
                    {(result.latencyMs / 1000).toFixed(1)} с
                  </dd>
                </div>
                <div aria-hidden="true">|</div>
                <div className="flex items-baseline gap-1">
                  <dt>токенов:</dt>
                  <dd className="tabular-nums text-fg-muted">{result.tokens}</dd>
                </div>
              </dl>
              <button
                type="button"
                onClick={copyAnswer}
                className="brikko-cta-secondary w-fit"
                data-testid="playground-copy"
              >
                {copied ? (
                  <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <Copy className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                )}
                <span>{copied ? 'Скопировано' : 'Скопировать ответ'}</span>
              </button>
            </div>
          ) : null}
        </div>
      </section>

      {showSignupModal ? (
        <SignupCtaModal
          onClose={() => setShowSignupModal(false)}
          onSignupClick={() =>
            track('playground_signup_cta_clicked', {
              from: 'success_modal',
              after_n_requests: successCount,
            })
          }
        />
      ) : null}
    </div>
  );
}

function PlaygroundErrorView({
  error,
  onRetry,
  onSignupClick,
}: {
  error: ApiError;
  onRetry: () => void;
  onSignupClick: () => void;
}) {
  if (error.status === 429) {
    return (
      <div className="mt-4 flex flex-1 flex-col gap-4">
        <Banner
          variant="warning"
          title="Лимит 5 запросов в час исчерпан"
          description={
            <>
              Можешь подождать
              {error.retryAfterSec ? ` ~${Math.ceil(error.retryAfterSec / 60)} мин` : ''}{' '}
              или зарегистрироваться сейчас — даём 200 ₽ welcome-бонуса.
            </>
          }
          action={
            <Link
              href="/signup"
              onClick={onSignupClick}
              className="brikko-cta-primary"
            >
              <span>Зарегистрироваться</span>
            </Link>
          }
          data-testid="playground-error-429"
        />
      </div>
    );
  }

  if (error.status === 503) {
    return (
      <div className="mt-4 flex flex-1 flex-col gap-4">
        <Banner
          variant="warning"
          title="Sandbox временно недоступен"
          description="Дневной бюджет на бесплатные запросы исчерпан. Зарегистрируйся для собственного баланса — будет работать без лимитов."
          action={
            <Link
              href="/signup"
              onClick={onSignupClick}
              className="brikko-cta-primary"
            >
              <span>Зарегистрироваться</span>
            </Link>
          }
          data-testid="playground-error-503"
        />
      </div>
    );
  }

  if (error.status === 0) {
    return (
      <div className="mt-4 flex flex-1 flex-col gap-4">
        <Banner
          variant="error"
          title="Сеть недоступна"
          description="Проверь интернет и попробуй ещё раз."
          action={
            <button
              type="button"
              onClick={onRetry}
              data-testid="playground-retry"
              className="brikko-cta-secondary"
            >
              Повторить
            </button>
          }
          data-testid="playground-error-network"
        />
      </div>
    );
  }

  return (
    <div className="mt-4 flex flex-1 flex-col gap-4">
      <Banner
        variant="error"
        title="Что-то пошло не так"
        description="Попробуй другую модель или повтори запрос."
        action={
          <button
            type="button"
            onClick={onRetry}
            data-testid="playground-retry"
            className="brikko-cta-secondary"
          >
            Повторить
          </button>
        }
        data-testid="playground-error-5xx"
      />
    </div>
  );
}

function SignupCtaModal({
  onClose,
  onSignupClick,
}: {
  onClose: () => void;
  onSignupClick: () => void;
}) {
  // Лёгкий собственный modal вместо <Dialog/> чтобы не тащить radix-dialog
  // в bundle лендинга. Нет фокус-trap'а — для single-action CTA это OK,
  // esc закрывает через keydown ниже.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="playground-cta-title"
      className="fixed inset-0 z-[150] flex items-center justify-center bg-[var(--fg-primary)]/40 p-4"
      onClick={onClose}
      data-testid="playground-signup-modal"
    >
      <div
        className="w-full max-w-md rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)] p-7 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3
          id="playground-cta-title"
          className="text-xl font-semibold text-fg-primary"
        >
          Хочешь больше?
        </h3>
        <p className="mt-3 text-body text-fg-muted">
          Зарегистрируйся за 5 минут — получишь{' '}
          <span className="font-semibold text-fg-primary">200 ₽ welcome-бонуса</span>{' '}
          и доступ ко всем 38 моделям без лимита 5/час.
        </p>
        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-4 py-2 text-body-sm font-medium text-fg-muted transition-colors hover:text-fg-primary"
          >
            Не сейчас
          </button>
          <Link
            href="/signup"
            onClick={onSignupClick}
            className="brikko-cta-primary"
          >
            <span>Зарегистрироваться</span>
            <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
          </Link>
        </div>
      </div>
    </div>
  );
}
