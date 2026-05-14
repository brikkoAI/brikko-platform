'use client';

/**
 * Interactive Smart Router demo.
 *
 * Backend /v1/public/playground принимает только sandbox-whitelist (6 SKU,
 * жёсткий Pydantic Literal — auto:* там не пройдёт). Поэтому здесь
 * стратегия → cheap-модель резолвится **на клиенте** по детерминированной
 * таблице, и пользователю показывается "что выбрал router". Это не
 * ground-truth router (тот живёт в auth-API), но даёт честную preview-демку:
 * мы публично заявляем "auto:cheap → DeepSeek по дефолту", и UI это
 * подтверждает. Когда откроем /v1/public/playground для auto:* (или
 * сделаем отдельный /v1/public/route-decision endpoint) — заменим mapping
 * на server-side decision.
 *
 * UX: малая double-bezel карточка по центру под Cookbook. textarea +
 * select, primary CTA, после клика — карточка-ответ "Выбрана модель X,
 * latency Y, оценочно Z ₽". Rate-limit (429) → CTA на регистрацию с
 * welcome-200₽-бонусом.
 */

import { useState } from 'react';
import styles from './RouterDemo.module.css';

type Strategy = 'auto:cheap' | 'auto:smart' | 'auto:fast' | 'auto:code';

type SandboxModelId =
  | 'deepseek-v4-flash'
  | 'gpt-5.4-mini'
  | 'claude-haiku-4.5'
  | 'gemini-3-flash'
  | 'yandexgpt-5-lite'
  | 'gigachat-2-lite';

const STRATEGY_OPTIONS: { value: Strategy; label: string; hint: string }[] = [
  { value: 'auto:cheap', label: 'auto:cheap', hint: 'минимальная цена' },
  { value: 'auto:smart', label: 'auto:smart', hint: 'максимальное качество' },
  { value: 'auto:fast', label: 'auto:fast', hint: 'минимальная latency' },
  { value: 'auto:code', label: 'auto:code', hint: 'для генерации кода' },
];

const STRATEGY_TO_MODEL: Record<Strategy, SandboxModelId> = {
  'auto:cheap': 'deepseek-v4-flash',
  'auto:smart': 'claude-haiku-4.5',
  'auto:fast': 'gemini-3-flash',
  'auto:code': 'gpt-5.4-mini',
};

const MODEL_DISPLAY: Record<SandboxModelId, { name: string; provider: string; rubPer1k: number }> = {
  'deepseek-v4-flash': { name: 'DeepSeek V4 Flash', provider: 'DeepSeek', rubPer1k: 12 },
  'gpt-5.4-mini': { name: 'GPT-5.4 mini', provider: 'OpenAI', rubPer1k: 18 },
  'claude-haiku-4.5': { name: 'Claude Haiku 4.5', provider: 'Anthropic', rubPer1k: 28 },
  'gemini-3-flash': { name: 'Gemini 3 Flash', provider: 'Google', rubPer1k: 14 },
  'yandexgpt-5-lite': { name: 'YandexGPT 5 Lite', provider: 'Яндекс', rubPer1k: 22 },
  'gigachat-2-lite': { name: 'GigaChat 2 Lite', provider: 'Сбер', rubPer1k: 24 },
};

type Decision = {
  modelId: SandboxModelId;
  latencyMs: number;
  costRub: number;
  reply: string;
};

type DemoState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'success'; decision: Decision }
  | { kind: 'rate_limited' }
  | { kind: 'error'; message: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'https://api.brikko.ru/v1';

function formatRub(rub: number): string {
  return rub.toFixed(2).replace('.', ',');
}

export function RouterDemo() {
  const [prompt, setPrompt] = useState('');
  const [strategy, setStrategy] = useState<Strategy>('auto:cheap');
  const [state, setState] = useState<DemoState>({ kind: 'idle' });

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || state.kind === 'loading') return;

    const modelId = STRATEGY_TO_MODEL[strategy];
    setState({ kind: 'loading' });

    const t0 = performance.now();
    try {
      const res = await fetch(`${API_BASE}/public/playground`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId, prompt: prompt.trim().slice(0, 500) }),
      });

      if (res.status === 429) {
        setState({ kind: 'rate_limited' });
        return;
      }

      if (!res.ok) {
        setState({
          kind: 'error',
          message:
            res.status === 503
              ? 'Sandbox временно недоступен. Попробуйте через минуту.'
              : `Ошибка ${res.status}. Попробуйте другой prompt.`,
        });
        return;
      }

      const data = (await res.json()) as {
        choices?: { message?: { content?: string } }[];
        usage?: { total_tokens?: number };
        model?: string;
      };

      const latencyMs = Math.round(performance.now() - t0);
      const totalTokens = data.usage?.total_tokens ?? Math.ceil(prompt.length / 3) + 50;
      const costRub = (totalTokens / 1000) * MODEL_DISPLAY[modelId].rubPer1k;
      const reply = data.choices?.[0]?.message?.content?.slice(0, 320) ?? '—';

      setState({
        kind: 'success',
        decision: { modelId, latencyMs, costRub, reply },
      });
    } catch (err) {
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'Сетевая ошибка',
      });
    }
  };

  const reset = () => setState({ kind: 'idle' });

  const display = state.kind === 'success' ? MODEL_DISPLAY[state.decision.modelId] : null;

  return (
    <section id="router-demo" className={styles.section} data-reveal-section>
      <header className={styles.sectionHeader}>
        <span className={styles.eyebrow}>
          <span className={styles.eyebrowDot} aria-hidden="true" />
          05 — Попробуй
        </span>
        <h2 className={styles.h2}>
          <span>Что выберет наш </span>
          <span className={styles.h2Italic}>router?</span>
        </h2>
        <p className={styles.lede}>
          Опишите задачу — увидите модель, которую auto-стратегия выбрала бы для платного запроса.
          Без регистрации, лимит 5 запросов в час.
        </p>
      </header>

      <div className={styles.cardOuter}>
        <div className={styles.cardInner}>
          <form onSubmit={onSubmit} className={styles.form}>
            <label className={styles.label} htmlFor="router-demo-prompt">
              <span className={styles.labelText}>Задача</span>
              <textarea
                id="router-demo-prompt"
                className={styles.textarea}
                placeholder="Например, перевод документа на английский"
                rows={3}
                maxLength={500}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={state.kind === 'loading'}
                required
              />
            </label>

            <label className={styles.label} htmlFor="router-demo-strategy">
              <span className={styles.labelText}>Стратегия</span>
              <select
                id="router-demo-strategy"
                className={styles.select}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value as Strategy)}
                disabled={state.kind === 'loading'}
              >
                {STRATEGY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label} — {opt.hint}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="submit"
              className={styles.cta}
              disabled={!prompt.trim() || state.kind === 'loading'}
            >
              {state.kind === 'loading' ? 'Считаем…' : 'Узнать выбор'}
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </button>
          </form>

          {state.kind === 'success' && display && (
            <div className={styles.result} role="status">
              <div className={styles.resultHeader}>
                <span className={styles.resultEyebrow}>Router выбрал</span>
                <button type="button" className={styles.resultReset} onClick={reset}>
                  Ещё раз
                </button>
              </div>
              <div className={styles.resultModel}>
                <strong>{display.name}</strong>
                <span className={styles.resultMeta}>
                  {display.provider} · {state.decision.latencyMs}ms · ~{formatRub(state.decision.costRub)} ₽
                </span>
              </div>
              <pre className={styles.resultReply}>{state.decision.reply}</pre>
            </div>
          )}

          {state.kind === 'rate_limited' && (
            <div className={styles.rateLimit} role="status">
              <strong>Лимит исчерпан.</strong> Попробуй через час бесплатно или{' '}
              <a href="/signup" className={styles.rateLimitLink}>
                зарегистрируйся и получи 200 ₽ на старте
              </a>
              .
            </div>
          )}

          {state.kind === 'error' && (
            <div className={styles.error} role="alert">
              {state.message}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
