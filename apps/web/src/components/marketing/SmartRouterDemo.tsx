'use client';

import { useMemo, useState } from 'react';
import {
  Sparkles,
  MessagesSquare,
  Brain,
  Code2,
  FileText,
  ArrowRight,
  ExternalLink,
} from 'lucide-react';
import {
  classifyDemo,
  CATEGORY_LABELS,
  type RouterCategory,
  type RouterDecision,
} from '@/lib/router-demo';

/**
 * SmartRouterDemo — интерактивная секция «Smart Router в действии».
 *
 * UX-обоснование:
 *   - Текстовое описание в Features не передаёт «вау». Видео-демо требует продакшна
 *     и привязано к конкретным моделям. Интерактивная демка с client-side
 *     классификацией: 0 серверной нагрузки, мгновенный отклик, пользователь
 *     САМ управляет инпутом → ощущение «у меня в руках реальный инструмент».
 *   - 4 chips сверху textarea ускоряют exploration: гость не обязан печатать,
 *     один клик → видит другую категорию.
 *   - Decision panel живёт в правой колонке и обновляется через aria-live=polite,
 *     чтобы screenreader проговаривал результат сразу после клика.
 *   - Никакого реального LLM-вызова: мы НЕ хотим, чтобы лендинг тратил наши токены
 *     на ботов и любопытных. Demo classifier purely client-side.
 *
 * Спецификация: задача из 02_Product, Sprint 11. Помещается между Features и
 * ComparisonTable — после того как гость прочитал «что отличает Brikko»,
 * показываем главный moat в действии.
 */

const CATEGORY_ICON: Record<RouterCategory, JSX.Element> = {
  chat: <MessagesSquare className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
  reasoning: <Brain className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
  code: <Code2 className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
  long_context: <FileText className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
};

interface ChipExample {
  label: string;
  prompt: string;
}

const EXAMPLES: ChipExample[] = [
  {
    label: 'Классификация',
    prompt:
      'Классифицируй: эта почта — спам? Tekst письма ниже.\n\n«Уважаемый клиент, ваш аккаунт заблокирован, перейдите по ссылке для разблокировки: bit.ly/xyz123».',
  },
  {
    label: 'Длинный контекст',
    prompt:
      'Ниже выписка по счёту за 3 года (200 000+ строк). Найди подозрительные транзакции и сгруппируй по типу.\n\n' +
      Array.from({ length: 1200 }, (_, i) => `2025-${(i % 12) + 1}-15  ОПЛАТА  ${(Math.random() * 50000).toFixed(2)} RUB  contractor-${i}`).join('\n'),
  },
  {
    label: 'Reasoning',
    prompt:
      'Реши задачу шаг за шагом: фабрика производит 1200 виджетов в день, 8% брак. После апгрейда брак упал до 3%, но производительность выросла на 15%. Сколько годных виджетов в день стало вместо было? Рассуждай по шагам.',
  },
  {
    label: 'На русском о ПДн',
    prompt:
      'Напиши вежливый ответ клиенту Петрову Ивану Сергеевичу (тел +7 999 123 45 67) о том, что его заявка №А-2031 рассмотрена и одобрена. Стиль — деловой, но тёплый.',
  },
];

// EXAMPLES[0] существует at construction time, но TS strict требует явного guard.
const DEFAULT_PROMPT: string = EXAMPLES[0]?.prompt ?? '';

export function SmartRouterDemo() {
  const [input, setInput] = useState<string>(DEFAULT_PROMPT);
  // Снимок последнего «решения»: меняется только по клику, чтобы пользователь
  // не видел дёргающуюся панель пока печатает.
  const [submitted, setSubmitted] = useState<string>(DEFAULT_PROMPT);

  const decision: RouterDecision = useMemo(
    () => classifyDemo(submitted),
    [submitted],
  );

  const runClassify = (text: string) => {
    setSubmitted(text);
  };

  const handleChip = (prompt: string) => {
    setInput(prompt);
    runClassify(prompt);
  };

  return (
    <section className="bg-white py-16 lg:py-24" aria-labelledby="smart-router-demo-heading">
      <div className="mx-auto max-w-6xl px-6">
        <div className="max-w-3xl">
          <span className="inline-flex items-center gap-2 rounded-full bg-brand-50 px-3 py-1 text-body-sm font-medium text-brand-700">
            <Sparkles className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Smart Router
          </span>
          <h2
            id="smart-router-demo-heading"
            className="mt-4 text-3xl font-semibold tracking-tight text-gray-900"
          >
            Покажем как Smart Router работает
          </h2>
          <p className="mt-3 text-body-large text-gray-700">
            Введи свой промпт — увидишь, как router его классифицирует и какую модель выберет
            под стратегию <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-body-sm text-gray-900">auto:cheap</code>.
          </p>
        </div>

        <div className="mt-10 grid gap-6 lg:grid-cols-2">
          {/* LEFT: input */}
          <div className="flex flex-col">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Готовые примеры промптов">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  type="button"
                  onClick={() => handleChip(ex.prompt)}
                  className="rounded-full border border-gray-300 bg-white px-3 py-1.5 text-body-sm font-medium text-gray-700 transition-colors hover:border-brand-600 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-2"
                >
                  {ex.label}
                </button>
              ))}
            </div>

            <label htmlFor="router-demo-input" className="sr-only">
              Промпт для классификации Smart Router&apos;ом
            </label>
            <textarea
              id="router-demo-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              aria-label="Промпт для классификации"
              spellCheck={false}
              className="mt-4 min-h-[220px] w-full resize-y rounded-lg border border-gray-300 bg-white p-4 font-mono text-body-sm leading-relaxed text-gray-900 shadow-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
            />

            <div className="mt-4 flex items-center justify-between gap-3">
              <p className="text-body-sm text-gray-500">
                Demo считает {Math.ceil(input.length / 3).toLocaleString('ru-RU')} токенов — оценка по символам, без реального tokenizer&apos;а.
              </p>
              <button
                type="button"
                onClick={() => runClassify(input)}
                className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2 text-body-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-2"
              >
                Показать решение router&apos;а
                <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* RIGHT: decision panel */}
          <div
            role="region"
            aria-live="polite"
            aria-label="Решение Smart Router'а"
            className="rounded-lg border border-gray-200 bg-gray-50 p-6"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-body-large font-semibold text-gray-900">Решение router&apos;а</h3>
              <span className="rounded-md bg-white px-2 py-1 font-mono text-body-sm text-gray-700">
                strategy: <span className="text-brand-700">{decision.strategy}</span>
              </span>
            </div>

            <dl className="mt-5 grid grid-cols-2 gap-3">
              <div className="rounded-md border border-gray-200 bg-white p-3">
                <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
                  Категория
                </dt>
                <dd className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-body-sm font-semibold text-brand-700">
                  {CATEGORY_ICON[decision.category]}
                  {CATEGORY_LABELS[decision.category]}
                </dd>
              </div>
              <div className="rounded-md border border-gray-200 bg-white p-3">
                <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
                  Контекст (оценка)
                </dt>
                <dd className="mt-1 font-mono text-body font-semibold text-gray-900">
                  {decision.estimatedTokens.toLocaleString('ru-RU')} токенов
                </dd>
              </div>
            </dl>

            <div className="mt-5">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Выбранная модель
              </p>
              <div className="mt-2 rounded-lg border-2 border-brand-600 bg-white p-4">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-mono text-body-large font-semibold text-gray-900">
                    {decision.primary.id}
                  </p>
                  <span className="rounded-md bg-brand-50 px-2 py-0.5 text-body-sm font-medium text-brand-700">
                    {decision.primary.provider}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-body-sm text-gray-700">
                  <span>
                    <span className="text-gray-500">Цена:</span>{' '}
                    <span className="font-semibold text-gray-900">
                      {decision.primary.pricePerMillionRub} ₽
                    </span>{' '}
                    / 1M токенов
                  </span>
                  <span>
                    <span className="text-gray-500">Контекст:</span>{' '}
                    <span className="font-semibold text-gray-900">
                      {decision.primary.contextK}K
                    </span>
                  </span>
                </div>
              </div>
            </div>

            <div className="mt-4">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Fallback chain
              </p>
              <ul className="mt-2 flex flex-wrap gap-2" aria-label="Резервные модели">
                {decision.fallback.map((m) => (
                  <li key={m.id}>
                    <span className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1 font-mono text-body-sm text-gray-700">
                      {m.id}
                      <span className="text-gray-400">·</span>
                      <span className="text-gray-500">{m.provider}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <p className="mt-5 rounded-md bg-white p-4 text-body-sm leading-relaxed text-gray-700">
              {decision.explanation}
            </p>
          </div>
        </div>

        <p className="mt-8 text-body-sm text-gray-500">
          Это упрощённая копия алгоритма для демо. Полная логика — в open-source коде:{' '}
          <a
            href="/docs/smart-routing"
            className="inline-flex items-center gap-1 font-medium text-brand-700 underline-offset-2 hover:underline"
          >
            документация Smart Router
            <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          </a>
          .
        </p>
      </div>
    </section>
  );
}
