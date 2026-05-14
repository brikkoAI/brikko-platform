'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { useState } from 'react';
import { ArrowRight, Check, Copy, Key, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from '@/components/ui/toast';
import { BRAND } from '@/lib/brand';
import { toKopecks } from '@/lib/types';
import { formatKopecks } from '@/lib/utils';
import { cn } from '@/lib/utils';

/**
 * Empty-state онбординг для нового юзера на /app.
 *
 * Когда: keys.length === 0 && transactions.length === 0 (juзер только что подтвердил
 * email и ничего ещё не делал, на счету ровно welcome-бонус 200 ₽).
 *
 * Цель — поднять утилизацию welcome-бонуса с ~70% (issue: пустой dashboard выглядит
 * тревожно, юзер не понимает с чего начать). Активирующий Bento layout вместо
 * «0 запросов / 0 ключей» в трёх карточках.
 *
 * UX-обоснование Bento (3-zone layout) vs одиночный hero:
 *   - Top hero — single CTA path («3 шага до первого запроса»). Снимает paralysis-by-choice.
 *   - Bottom-left — show, don't tell: реальные use-кейсы с цифрами доказывают «оно правда работает за рубль».
 *   - Bottom-right — Smart Router teaser: главный USP, но не центральный — как «kicker» сбоку.
 *
 * Палитра — существующая прод-индиго (#4338CA brand-700). Это НЕ rebrand-проект,
 * только активирующий empty-state. Tier 3 grayscale-rebrand — отдельный roadmap.
 */

interface EmptyDashboardWelcomeProps {
  /**
   * Баланс в копейках для отображения в hero. Принимаем сырое число (баланс приходит
   * с backend без brand-типа); внутри приводим к Kopecks для formatKopecks.
   */
  balanceKopecks: number;
  /** Открывает CreateKeyDialog в parent'е. */
  onCreateKey: () => void;
  /** Имя юзера для приветствия (опционально — фолбэк «Привет!»). */
  userName?: string;
}

const PLACEHOLDER_KEY = 'sk-brk-XXXXXXXXXXXX';

const SAMPLE_CURL = `curl https://${BRAND.apiDomain}/v1/chat/completions \\
  -H "Authorization: Bearer ${PLACEHOLDER_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto:cheap",
    "messages": [{"role": "user", "content": "Привет"}]
  }'`;

interface UseCase {
  slug: string;
  title: string;
  modelLabel: string;
  costPer1000: number;
}

// Source: src/content/cookbook/*.md frontmatter (estimated_cost_per_1000_calls).
// Не дёргаем cookbook.ts (server-only fs read) — карточка client-side, статичные значения.
const USE_CASES: readonly UseCase[] = [
  {
    slug: 'contract-pii-redaction',
    title: 'Анализ договоров',
    modelLabel: 'Claude Sonnet',
    costPer1000: 980,
  },
  {
    slug: 'crm-lead-classification',
    title: 'Классификация лидов',
    modelLabel: 'DeepSeek',
    costPer1000: 12,
  },
  {
    slug: 'email-followup-generation',
    title: 'Follow-up email',
    modelLabel: 'Smart Router',
    costPer1000: 220,
  },
] as const;

export function EmptyDashboardWelcome({
  balanceKopecks,
  onCreateKey,
  userName,
}: EmptyDashboardWelcomeProps) {
  const [copied, setCopied] = useState(false);

  const greeting = userName ? `Привет, ${userName}!` : 'Привет!';
  // formatKopecks accepts только brand'ный Kopecks — оборачиваем raw number из props.
  const balanceLabel = formatKopecks(toKopecks(balanceKopecks), { forceFraction: false });

  async function handleCopyCurl() {
    try {
      await navigator.clipboard.writeText(SAMPLE_CURL);
      setCopied(true);
      toast.success('Скопировано');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Не удалось скопировать');
    }
  }

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="empty-dashboard-welcome"
    >
      {/* ──────────────────────────────────────────────────────────────────
       *  HERO — full-width activation card.
       *  3 numbered shortcuts. Single visual hierarchy = single decision.
       * ────────────────────────────────────────────────────────────────── */}
      <FadeInUp delay={0}>
        <Card className="relative overflow-hidden border-brand-100 bg-gradient-to-br from-brand-50/60 via-white to-white p-8 md:p-10">
          <div className="relative z-10">
            <p className="text-body-sm font-medium uppercase tracking-[0.18em] text-brand-700">
              01 — Добро пожаловать
            </p>
            <h2 className="mt-3 max-w-2xl text-3xl font-medium leading-tight text-gray-900 md:text-[2.75rem] md:leading-[1.1]">
              {greeting} У вас{' '}
              <span className="font-semibold text-brand-700 tabular-nums">
                {balanceLabel}
              </span>{' '}
              на счету.
            </h2>
            <p className="mt-3 max-w-xl text-body-large text-gray-600">
              Давайте сделаем первый запрос за 3 минуты.
            </p>

            <ol className="mt-8 flex flex-col gap-3" data-testid="welcome-shortcuts">
              <ShortcutRow
                index={1}
                title="Создать API-ключ"
                hint="Один ключ — доступ ко всем 38 моделям."
                cta="Создать"
                onClick={onCreateKey}
                testId="welcome-shortcut-create-key"
              />
              <ShortcutRow
                index={2}
                title="Скопировать curl-сниппет"
                hint="Готовый запрос с auto:cheap-роутером."
                customRight={
                  <Button
                    type="button"
                    size="sm"
                    variant={copied ? 'secondary' : 'primary'}
                    onClick={handleCopyCurl}
                    leftIcon={
                      copied ? (
                        <Check className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Copy className="h-4 w-4" aria-hidden="true" />
                      )
                    }
                    data-testid="welcome-shortcut-copy-curl"
                    aria-label="Скопировать curl"
                  >
                    {copied ? 'Скопировано' : 'Скопировать'}
                  </Button>
                }
              />
              <ShortcutRow
                index={3}
                title="Запустить в Playground"
                hint="Без терминала — прямо в браузере."
                cta="Открыть"
                href="/playground"
                testId="welcome-shortcut-playground"
              />
            </ol>

            {/* Curl preview window — dark mock. Decorative; копирует та же кнопка выше. */}
            <div className="mt-6 overflow-hidden rounded-xl border border-gray-800/40 bg-[#1A1A1A] shadow-md">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-2">
                <div className="flex items-center gap-1.5" aria-hidden="true">
                  <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
                  <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
                  <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
                </div>
                <span className="font-mono text-[11px] uppercase tracking-wider text-white/40">
                  curl · auto:cheap
                </span>
              </div>
              <pre className="overflow-x-auto p-4 font-mono text-code leading-relaxed text-white/90">
                <code>{SAMPLE_CURL}</code>
              </pre>
            </div>
          </div>
        </Card>
      </FadeInUp>

      {/* ──────────────────────────────────────────────────────────────────
       *  BOTTOM ROW: use-cases (col-span-8) + smart-router (col-span-4).
       *  Bento композиция — два разных «голоса» на одной линии.
       * ────────────────────────────────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-12">
        <FadeInUp delay={120} className="lg:col-span-8">
          <Card className="h-full">
            <header className="mb-5">
              <p className="text-body-sm font-medium uppercase tracking-[0.18em] text-gray-500">
                02 — Что мы умеем
              </p>
              <h3 className="mt-2 text-xl font-semibold text-gray-900">
                Готовые рецепты с расчётом стоимости
              </h3>
              <p className="mt-1 text-body-sm text-gray-500">
                Скопировать код, поменять API-ключ — и в продакшен.
              </p>
            </header>

            <ul className="flex flex-col">
              {USE_CASES.map((uc, i) => (
                <li key={uc.slug}>
                  <Link
                    href={`/docs/cookbook/${uc.slug}` as Route}
                    className={cn(
                      'group flex items-center gap-4 py-4 transition-all duration-150',
                      'hover:translate-x-1 hover:border-brand-200',
                      i !== USE_CASES.length - 1 && 'border-b border-gray-100',
                    )}
                    data-testid={`welcome-usecase-${uc.slug}`}
                  >
                    <div className="flex flex-1 flex-col gap-0.5">
                      <p className="text-body font-medium text-gray-900">
                        {uc.title}
                      </p>
                      <p className="text-body-sm text-gray-500">
                        {uc.modelLabel} ·{' '}
                        <span className="tabular-nums">
                          ~{uc.costPer1000} ₽
                        </span>{' '}
                        за 1 000 запросов
                      </p>
                    </div>
                    <ArrowRight
                      className="h-4 w-4 text-gray-400 transition-colors group-hover:text-brand-600"
                      aria-hidden="true"
                    />
                  </Link>
                </li>
              ))}
            </ul>

            <div className="mt-4 border-t border-gray-100 pt-4">
              <Link
                href="/docs/cookbook"
                className="text-body-sm font-medium text-brand-700 hover:underline"
              >
                Все рецепты →
              </Link>
            </div>
          </Card>
        </FadeInUp>

        <FadeInUp delay={240} className="lg:col-span-4">
          <Card className="flex h-full flex-col bg-gray-900 text-white border-gray-900">
            <header className="mb-5 flex items-start gap-3">
              <span
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10"
                aria-hidden="true"
              >
                <Zap className="h-4 w-4 text-brand-300" />
              </span>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-white/50">
                  03 — Smart Router
                </p>
                <h3 className="mt-1 text-lg font-semibold">
                  Один ключ — все модели
                </h3>
              </div>
            </header>

            <ul className="flex flex-1 flex-col gap-3 text-body-sm text-white/80">
              <li className="flex gap-2">
                <span className="text-brand-300" aria-hidden="true">
                  →
                </span>
                <span>
                  <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[12px] text-white">
                    auto:cheap
                  </code>{' '}
                  — самая дешёвая модель под задачу
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-brand-300" aria-hidden="true">
                  →
                </span>
                <span>
                  <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[12px] text-white">
                    auto:smart
                  </code>{' '}
                  — лучшее качество за свою цену
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-brand-300" aria-hidden="true">
                  →
                </span>
                <span>Failover на резерв, если основной провайдер упал</span>
              </li>
            </ul>

            <Link
              href="/docs/smart-routing"
              className="mt-6 inline-flex items-center gap-1.5 text-body-sm font-medium text-brand-300 hover:text-brand-200"
            >
              Как работает роутер
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </Card>
        </FadeInUp>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  Subcomponents
// ─────────────────────────────────────────────────────────────────────────────

interface ShortcutRowProps {
  index: number;
  title: string;
  hint: string;
  cta?: string;
  href?: Route | string;
  onClick?: () => void;
  customRight?: React.ReactNode;
  testId?: string;
}

function ShortcutRow({
  index,
  title,
  hint,
  cta,
  href,
  onClick,
  customRight,
  testId,
}: ShortcutRowProps) {
  return (
    <li
      className={cn(
        'flex items-center gap-4 rounded-xl border border-gray-200 bg-white px-4 py-3',
        'transition-all duration-150 hover:translate-x-1 hover:border-brand-300 hover:shadow-sm',
      )}
    >
      <span
        aria-hidden="true"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-100 text-body-sm font-semibold tabular-nums text-brand-700"
      >
        {index === 1 ? <Key className="h-4 w-4" /> : index}
      </span>
      <div className="flex flex-1 flex-col">
        <p className="text-body font-medium text-gray-900">{title}</p>
        <p className="text-body-sm text-gray-500">{hint}</p>
      </div>
      {customRight ? (
        customRight
      ) : href ? (
        <Button asChild size="sm" data-testid={testId}>
          <Link href={href as Route}>
            {cta}
            <ArrowRight className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </Button>
      ) : (
        <Button size="sm" onClick={onClick} data-testid={testId}>
          {cta}
          <ArrowRight className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      )}
    </li>
  );
}

/**
 * Staggered fade-in: appearance-only анимация при первом маунте. Использует
 * inline-стили, чтобы не плодить keyframes в globals.css ради одного места.
 * `prefers-reduced-motion` уважается через глобальное правило в globals.css §base.
 */
function FadeInUp({
  delay,
  className,
  children,
}: {
  delay: number;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn('welcome-fade-in', className)}
      style={{
        animationDelay: `${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}
