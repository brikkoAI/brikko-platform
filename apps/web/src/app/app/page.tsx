'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Key } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Banner } from '@/components/ui/banner';
import { Skeleton } from '@/components/ui/skeleton';
import { ActivityFeed } from '@/components/dashboard/ActivityFeed';
import { BalanceCard } from '@/components/dashboard/BalanceCard';
import { CreateKeyDialog } from '@/components/dashboard/CreateKeyDialog';
import { EmptyDashboardWelcome } from '@/components/dashboard/EmptyDashboardWelcome';
import { GetStartedChecklist } from '@/components/dashboard/GetStartedChecklist';
import { QuickStart } from '@/components/dashboard/QuickStart';
import { WelcomeModal } from '@/components/dashboard/WelcomeModal';
import {
  useAccount,
  useApiKeys,
  useTransactions,
  useUsage,
} from '@/lib/auth';
import { formatKopecks, formatTokens } from '@/lib/utils';
import { getDescription } from '@/lib/transactions';
import { track } from '@/lib/analytics';
import type { ApiKeyCreated } from '@/lib/types';

/**
 * Overview-страница (`/app`). Решает 3 задачи:
 *  1. Welcome-state для нового юзера: пустая БД → банер «Создай ключ + curl-snippet».
 *  2. Активного юзера: баланс, расход за 7 дней, активные ключи.
 *  3. Низкий баланс <500 ₽: banner warning над всем (см. microcopy §2.1).
 *
 * UX-выбор: одна страница вместо отдельных welcome/dashboard — пользователь не «видит»
 * тумблер режима, контент сам перестраивается по данным. Это паттерн Stripe / Linear.
 */

const SEVEN_DAYS_AGO = () => new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
const TODAY = () => new Date().toISOString().slice(0, 10);
const LOW_BALANCE_KOP = 50_000; // 500 ₽

export default function OverviewPage() {
  const account = useAccount();
  const keys = useApiKeys();
  const tx = useTransactions({ limit: 5 });
  const usage = useUsage({ from: SEVEN_DAYS_AGO(), to: TODAY(), group_by: 'day' });

  const checklistRef = useRef<HTMLDivElement>(null);
  const quickStartRef = useRef<HTMLDivElement>(null);
  const [createKeyOpen, setCreateKeyOpen] = useState(false);
  /**
   * Sprint 12 §1 — после создания ключа QuickStart pre-fills curl с full_key.
   * Храним секрет ровно в этом state на время сессии страницы; при unmount или
   * F5 — теряется (security: на бэке секрет hash-only, мы его никуда не шлём).
   */
  const [lastCreatedKey, setLastCreatedKey] = useState<ApiKeyCreated | null>(null);

  const activeKeys = useMemo(
    () => (keys.data ?? []).filter((k) => !k.revoked_at),
    [keys.data],
  );
  // Real backend возвращает next_cursor вместо total — если total отсутствует,
  // деривируем из items.length (для welcome-state важна сама малость списка).
  // Любое поле может отсутствовать (legacy-backend / partial-fetch) — поэтому
  // optional chain до items, не прямой `.length`.
  // useMemo: иначе useEffect ниже перезапускается каждый рендер (eslint react-hooks/exhaustive-deps).
  const txItems = useMemo(() => tx.data?.items ?? [], [tx.data?.items]);
  const txCount = tx.data?.total ?? txItems.length;
  const isWelcomeState = activeKeys.length === 0 && txCount <= 1;

  // Sprint 12.5+ activation: пустой first-visit state. Rule: 0 ключей AND 0 usage-событий.
  // Намеренно учитываем именно `usage` (не любую транзакцию) — у юзера ровно
  // welcome_credit, любой topup тоже выкинет нас в обычный dashboard. Edge: если
  // юзер сделал запрос через playground (один usage-tx) но ключа всё ещё нет —
  // показываем обычный dashboard, потому что usage-track уже сработал, и пустой
  // welcome выглядит fake'ом. См. отчёт «edge case» ниже.
  const hasUsageEvent = useMemo(
    () => txItems.some((t) => t.type === 'usage'),
    [txItems],
  );
  const isFirstVisitEmpty =
    !keys.isLoading &&
    !tx.isLoading &&
    activeKeys.length === 0 &&
    !hasUsageEvent;

  const balance = account.data?.balance_kopecks ?? 0;
  const showLowBalanceBanner = !account.isLoading && balance > 0 && balance < LOW_BALANCE_KOP;

  // Sentinel для usage-totals: backend (legacy / 5xx-fallback) может вернуть
  // объект без поля `totals` — без этого guard'а сложение падает с
  // "Cannot read properties of undefined". См. баг 2026-05-02 #1.
  const usageTotals = usage.data?.totals;

  // Sprint 12 §3 — analytics для signup→first_request воронки. Все эти события
  // дедуплицируются на стороне `track()` (sessionStorage) — повторный вызов
  // при F5 или re-render не создаст дубль. См. lib/analytics.ts.
  useEffect(() => {
    if (account.data?.email_verified) {
      // signup_completed = email verified — это и есть момент «активного» аккаунта.
      track('signup_completed', { account_id: account.data.account_id });
      track('email_verified');
    }
  }, [account.data?.email_verified, account.data?.account_id]);

  useEffect(() => {
    const hasUsage = txItems.some((t) => t.type === 'usage');
    if (hasUsage) {
      track('first_request_made');
    }
  }, [txItems]);

  // First-visit empty: rebuild dashboard как welcoming activation flow вместо
  // тревожной пустоты «0 / 0 / 200 ₽». Аналитика — событие `welcome_empty_state_shown`
  // уже покрыто signup_completed; отдельный track не нужен.
  if (isFirstVisitEmpty && account.data) {
    return (
      <div className="mx-auto flex max-w-5xl flex-col gap-6">
        <WelcomeModal
          onStart={() => {
            // С пустого dashboard'а скроллить некуда — оставляем no-op,
            // модалка просто закрывается. См. WelcomeModal §3.1.
          }}
        />

        <EmptyDashboardWelcome
          balanceKopecks={balance}
          userName={account.data.name?.split(' ')[0]}
          onCreateKey={() => setCreateKeyOpen(true)}
        />

        <CreateKeyDialog
          open={createKeyOpen}
          onOpenChange={setCreateKeyOpen}
          onCreated={(created) => {
            // После создания ключа — query-инвалидация автоматически выкинет
            // нас из isFirstVisitEmpty (keys.length > 0). Сохраняем ключ для
            // последующего pre-fill в QuickStart на «обычном» dashboard.
            setLastCreatedKey(created);
          }}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <WelcomeModal
        onStart={() => {
          // §3.1 Welcome → скроллим к Get Started checklist
          checklistRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }}
      />

      <div>
        <h1 className="text-3xl font-semibold text-gray-900">Обзор</h1>
        <p className="mt-2 text-body text-gray-500">
          Баланс, расход за 7 дней и быстрый старт. Всё, что нужно, чтобы запустить первый запрос.
        </p>
      </div>

      <div ref={checklistRef}>
        <GetStartedChecklist
          account={account.data ?? null}
          keys={keys.data ?? []}
          transactions={tx.data?.items ?? []}
          onCreateKey={() => setCreateKeyOpen(true)}
          onShowCurl={() => {
            quickStartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }}
        />
      </div>

      <CreateKeyDialog
        open={createKeyOpen}
        onOpenChange={setCreateKeyOpen}
        onCreated={(created) => {
          setLastCreatedKey(created);
          // Скроллим к QuickStart, чтобы юзер увидел готовый curl сразу после
          // закрытия модалки. UX-обоснование PRD §2 — между «закрыл модалку» и
          // «увидел curl с ключом» не должно быть лишних шагов.
          setTimeout(() => {
            quickStartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }, 50);
        }}
      />

      {balance === 0 && !account.isLoading ? (
        // §5.1 Insufficient balance — баланс на нуле, запросы возвращают 402.
        <Banner
          variant="error"
          title="На балансе 0 ₽ — запросы остановлены"
          description={<>Пополнить баланс, чтобы продолжить. Минимальное пополнение — 100 ₽.</>}
          action={
            <Button asChild size="sm">
              <Link href="/app/billing">Пополнить</Link>
            </Button>
          }
          data-testid="zero-balance-banner"
        />
      ) : showLowBalanceBanner ? (
        // Pre-MVP-warning: balance > 0, но < 500 ₽. Не §5.1 — это раннее предупреждение,
        // даём юзеру время, прежде чем запросы остановятся.
        <Banner
          variant="warning"
          title={`Баланс ${formatKopecks(balance)} — это ~${Math.floor(balance / 80)} запросов к gpt-5.4-mini.`}
          description={
            <>Пополни счёт или включи авто-пополнение, чтобы запросы не остановились.</>
          }
          action={
            <Button asChild size="sm">
              <Link href="/app/billing">Пополнить</Link>
            </Button>
          }
          data-testid="low-balance-banner"
        />
      ) : null}

      {isWelcomeState && account.data ? (
        <Banner
          variant="info"
          title="Готов к первому запросу"
          description={
            <>
              На балансе {formatKopecks(balance, { forceFraction: true })} welcome-бонуса. Создай
              ключ, скопируй curl-сниппет ниже и запусти в терминале — это всё.
            </>
          }
          data-testid="welcome-banner"
        />
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <BalanceCard />

        <Card>
          <CardDescription>Расход за 7 дней</CardDescription>
          {usage.isLoading ? (
            <Skeleton className="mt-1 h-9 w-24" />
          ) : (
            <CardTitle className="text-3xl tabular-nums">
              {usageTotals ? formatKopecks(usageTotals.cost_kop) : '— ₽'}
            </CardTitle>
          )}
          <p className="mt-3 text-body-sm text-gray-500" aria-live="polite">
            {usageTotals
              ? `${formatTokens((usageTotals.tokens_in ?? 0) + (usageTotals.tokens_out ?? 0))} токенов`
              : 'Нет данных за период'}
          </p>
        </Card>

        <Card>
          <CardDescription>Активные ключи</CardDescription>
          {keys.isLoading ? (
            <Skeleton className="mt-1 h-9 w-12" />
          ) : (
            <CardTitle className="text-3xl tabular-nums">{activeKeys.length}</CardTitle>
          )}
          <Button asChild variant="secondary" size="sm" className="mt-4" leftIcon={<Key className="h-4 w-4" />}>
            <Link href="/app/keys">Управлять ключами</Link>
          </Button>
        </Card>
      </div>

      <ActivityFeed />

      <div ref={quickStartRef}>
        <QuickStart apiKey={lastCreatedKey?.full_key} />
      </div>

      <section className="rounded-lg border border-gray-200 bg-white p-6">
        <header className="mb-4 flex items-end justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Последние транзакции</h2>
            <p className="text-body-sm text-gray-500">
              Пополнения, списания и подписки. Полный список — на{' '}
              <Link href="/app/billing" className="text-brand-600 hover:underline">
                странице биллинга
              </Link>
              .
            </p>
          </div>
        </header>
        {tx.isLoading ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : txItems.length === 0 ? (
          <p className="text-body-sm text-gray-500">
            История транзакций появится после первого пополнения.
          </p>
        ) : (
          <ul className="divide-y divide-gray-200">
            {txItems.slice(0, 5).map((t) => (
              <li key={t.id} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-body text-gray-900">{getDescription(t)}</p>
                  <p className="text-body-sm text-gray-500">
                    {new Date(t.created_at).toLocaleDateString('ru-RU', {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })}
                  </p>
                </div>
                <span
                  className={
                    t.amount_kopecks >= 0
                      ? 'text-body font-medium tabular-nums text-success-600'
                      : 'text-body font-medium tabular-nums text-gray-700'
                  }
                >
                  {t.amount_kopecks >= 0 ? '+' : ''}
                  {formatKopecks(t.amount_kopecks, { forceFraction: true })}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
