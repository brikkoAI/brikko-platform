'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { Check, Lock, Mail, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { Account, ApiKey, Transaction } from '@/lib/types';

const DISMISS_KEY = 'voltari.checklist_dismissed';
const TG_SKIP_KEY = 'voltari.checklist_tg_skipped';

type StepStatus = 'done' | 'pending' | 'locked' | 'skipped';

interface ChecklistStep {
  id: 1 | 2 | 3 | 4 | 5;
  title: string;
  description: string;
  status: StepStatus;
  cta?: { label: string; href?: string; onClick?: () => void; testId?: string };
}

interface GetStartedChecklistProps {
  account: Account | null | undefined;
  keys: readonly ApiKey[];
  transactions: readonly Transaction[];
  /** Открывает CreateKeyDialog. */
  onCreateKey?: () => void;
  /** Скроллит к QuickStart curl-блоку. */
  onShowCurl?: () => void;
  /** Кнопка «Отправить ещё раз» — отправляет письмо verify. */
  onResendEmail?: () => void;
}

/**
 * §3.2 Get Started — 5-шаговый чек-лист на главной dashboard.
 *
 * UX-обоснование:
 *   - Locked-шаги недоступны (CTA disabled) с tooltip — это удерживает юзера в правильном
 *     порядке (нет смысла «Пополнить» до создания ключа).
 *   - Auto-collapse при ≥ 4/5 — после 4-го шага юзер уже понял, как пользоваться продуктом,
 *     карточка просто занимает место. Сворачиваем до тонкой строки прогресса.
 *   - Dismiss × — confirm через простой confirm dialog (не требует state machine).
 */
export function GetStartedChecklist({
  account,
  keys,
  transactions,
  onCreateKey,
  onShowCurl,
  onResendEmail,
}: GetStartedChecklistProps) {
  const [dismissed, setDismissed] = useState<boolean>(false);
  const [tgSkipped, setTgSkipped] = useState<boolean>(false);
  const [collapsed, setCollapsed] = useState<boolean>(false);

  // Read persisted flags ровно один раз на маунте; SSR-safe.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      setDismissed(window.localStorage.getItem(DISMISS_KEY) === 'true');
      setTgSkipped(window.localStorage.getItem(TG_SKIP_KEY) === 'true');
    } catch {
      /* privacy mode — пропустим */
    }
  }, []);

  const steps = useMemo<ChecklistStep[]>(() => {
    const emailDone = Boolean(account?.email_verified);
    const hasActiveKey = keys.some((k) => !k.revoked_at);
    // «Сделать первый запрос» считаем done если есть usage-транзакция.
    const hasUsage = transactions.some((t) => t.type === 'usage');
    // Пополнение = topup (не welcome_credit).
    const hasTopup = transactions.some((t) => t.type === 'topup');
    const tgLinked = Boolean(account?.telegram_link?.linked);

    return [
      {
        id: 1,
        title: 'Подтвердить email',
        description: 'Открыть письмо и перейти по ссылке.',
        status: emailDone ? 'done' : 'pending',
        cta: emailDone ? undefined : { label: 'Отправить ещё раз', onClick: onResendEmail, testId: 'checklist-resend-email' },
      },
      {
        id: 2,
        title: 'Создать первый API-ключ',
        description: 'Один ключ — доступ ко всем 38 моделям.',
        status: hasActiveKey ? 'done' : !emailDone ? 'locked' : 'pending',
        cta:
          hasActiveKey || !emailDone
            ? undefined
            : { label: 'Создать ключ', onClick: onCreateKey, testId: 'checklist-create-key' },
      },
      {
        id: 3,
        title: 'Сделать первый запрос',
        description: 'Скопировать curl-сниппет и выполнить в терминале.',
        status: hasUsage ? 'done' : !hasActiveKey ? 'locked' : 'pending',
        cta:
          hasUsage || !hasActiveKey
            ? undefined
            : { label: 'Показать curl', onClick: onShowCurl, testId: 'checklist-show-curl' },
      },
      {
        id: 4,
        title: 'Пополнить баланс',
        description: 'На балансе 200 ₽ welcome-бонуса. Пополнить, чтобы продолжить после.',
        status: hasTopup ? 'done' : 'pending',
        cta: hasTopup ? undefined : { label: 'Пополнить', href: '/app/billing', testId: 'checklist-topup' },
      },
      {
        id: 5,
        title: 'Связать Telegram (опционально)',
        description: 'Алерты о балансе и платежах в @BrikkoAI_bot.',
        status: tgLinked ? 'done' : tgSkipped ? 'skipped' : 'pending',
        cta:
          tgLinked || tgSkipped
            ? undefined
            : { label: 'Подключить', href: '/app/settings#notifications', testId: 'checklist-tg' },
      },
    ];
  }, [account, keys, transactions, tgSkipped, onCreateKey, onShowCurl, onResendEmail]);

  const doneCount = steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;

  // Auto-collapse при ≥ 4/5 — см. §3.2.
  useEffect(() => {
    if (doneCount >= 4) setCollapsed(true);
  }, [doneCount]);

  if (dismissed) return null;

  function handleDismiss() {
    if (typeof window === 'undefined') {
      setDismissed(true);
      return;
    }
    const ok = window.confirm('Скрыть чек-лист. Если понадобится — вернуть в настройках.');
    if (!ok) return;
    try {
      window.localStorage.setItem(DISMISS_KEY, 'true');
    } catch {
      /* no-op */
    }
    setDismissed(true);
  }

  if (collapsed && doneCount < 5) {
    return (
      <Card className="flex items-center justify-between" data-testid="get-started-collapsed">
        <p className="text-body text-gray-700">
          Get Started — <span className="font-medium tabular-nums">{doneCount}/5</span> готово
        </p>
        <Button variant="ghost" size="sm" onClick={() => setCollapsed(false)}>
          Развернуть
        </Button>
      </Card>
    );
  }

  if (doneCount === 5) {
    return (
      <Card className="flex items-center justify-between" data-testid="get-started-done">
        <p className="text-body text-gray-700">Готово — все шаги выполнены.</p>
        <Button variant="ghost" size="sm" onClick={handleDismiss}>
          Скрыть
        </Button>
      </Card>
    );
  }

  return (
    <Card data-testid="get-started-checklist">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Get Started</h2>
          <p className="text-body-sm text-gray-500">Завершить настройку аккаунта.</p>
        </div>
        <div className="flex items-center gap-3">
          <p className="text-body-sm tabular-nums text-gray-500">{doneCount}/5 готово</p>
          <button
            type="button"
            onClick={handleDismiss}
            className="text-gray-400 hover:text-gray-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
            aria-label="Скрыть чек-лист"
            data-testid="checklist-dismiss"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-gray-100" aria-hidden="true">
        <div
          className="h-full bg-brand-600 transition-all duration-200"
          style={{ width: `${(doneCount / 5) * 100}%` }}
        />
      </div>

      <ol className="mt-4 flex flex-col gap-3">
        {steps.map((step) => (
          <ChecklistRow
            key={step.id}
            step={step}
            onSkipTg={
              step.id === 5
                ? () => {
                    try {
                      window.localStorage.setItem(TG_SKIP_KEY, 'true');
                    } catch {
                      /* no-op */
                    }
                    setTgSkipped(true);
                  }
                : undefined
            }
          />
        ))}
      </ol>
    </Card>
  );
}

function ChecklistRow({
  step,
  onSkipTg,
}: {
  step: ChecklistStep;
  onSkipTg?: () => void;
}) {
  const isLocked = step.status === 'locked';
  const isDone = step.status === 'done';
  const isSkipped = step.status === 'skipped';

  return (
    <li
      className={cn(
        'flex items-start gap-3 rounded-md border border-gray-200 p-3',
        isDone || isSkipped ? 'bg-gray-50' : 'bg-white',
      )}
      data-testid={`checklist-step-${step.id}`}
      data-status={step.status}
    >
      <span
        aria-hidden="true"
        className={cn(
          'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-body-sm font-medium',
          isDone && 'bg-success-50 text-success-600',
          isSkipped && 'bg-gray-100 text-gray-500',
          isLocked && 'bg-gray-100 text-gray-400',
          step.status === 'pending' && 'bg-brand-100 text-brand-700',
        )}
      >
        {isDone ? (
          <Check className="h-4 w-4" />
        ) : isLocked ? (
          <Lock className="h-3.5 w-3.5" />
        ) : step.id === 1 && step.status === 'pending' ? (
          <Mail className="h-3.5 w-3.5" />
        ) : (
          step.id
        )}
      </span>
      <div className="flex flex-1 flex-col gap-1">
        <p
          className={cn(
            'text-body font-medium',
            isLocked ? 'text-gray-400' : isDone || isSkipped ? 'text-gray-500' : 'text-gray-900',
          )}
        >
          {step.title}
        </p>
        <p
          className={cn(
            'text-body-sm',
            isLocked ? 'text-gray-400' : 'text-gray-500',
          )}
        >
          {isSkipped ? 'Пропущено. Вернуть в настройках.' : step.description}
        </p>
      </div>
      {step.cta && step.status === 'pending' ? (
        <div className="flex shrink-0 items-center gap-2">
          {step.cta.href ? (
            <Button asChild size="sm" data-testid={step.cta.testId}>
              <Link href={step.cta.href as Route}>{step.cta.label}</Link>
            </Button>
          ) : (
            <Button size="sm" onClick={step.cta.onClick} data-testid={step.cta.testId}>
              {step.cta.label}
            </Button>
          )}
          {step.id === 5 && onSkipTg ? (
            <Button variant="ghost" size="sm" onClick={onSkipTg} data-testid="checklist-skip-tg">
              Пропустить
            </Button>
          ) : null}
        </div>
      ) : null}
      {isLocked ? (
        <span className="shrink-0 self-center text-body-sm text-gray-400" title={`Сначала выполнить шаг ${step.id - 1}`}>
          Заблокировано
        </span>
      ) : null}
    </li>
  );
}
