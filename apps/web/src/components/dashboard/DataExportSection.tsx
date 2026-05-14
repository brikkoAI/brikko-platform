'use client';

import { useState } from 'react';
import { Download, Clock, Check, AlertTriangle, Mail, Loader2 } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  useDataExportLatest,
  useDataExportList,
  useDataExportStatus,
  useRequestDataExport,
} from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';
import { formatDateTime } from '@/lib/utils';
import type { DataExportRequest, DataExportStatus } from '@/lib/types';

/**
 * Data-export секция в Privacy — production-version (Sprint 7).
 *
 * UX-обоснование:
 *  - **Polling вместо WebSocket** — экспорты обычно готовятся 5-30 секунд, но иногда
 *    минуты (большие аккаунты). Polling с exponential backoff (5s → 30s) — прост
 *    в реализации, нагрузку держит в адекватных рамках (~120 req/час максимум),
 *    надёжнее WS на flaky-сетях.
 *  - **Inline download URL + email-fallback** — backend дублирует ссылку и в email,
 *    и на странице. Email — для долгих экспортов (юзер ушёл, потом увидел письмо);
 *    inline — для быстрых (юзер ждёт прямо сейчас, копировать ссылку из почты — лишний шаг).
 *  - **24h cooldown indicator** — показываем явно, чтобы юзер не давил кнопку и
 *    не получал 429 в виде ошибки. Disabled-state кнопки + text-hint работают лучше,
 *    чем «Запрос принят», который оказывается «нет, не принят».
 *  - **List of past exports** — компактный (top-5), потому что 95% юзеров делают
 *    1-2 экспорта в год; полная история — overkill. Expired URL'ы помечаются явно.
 *
 * Спека: 02_Product/v1.5 — Sprint 7 frontend brief.
 */

const COOLDOWN_MS = 24 * 60 * 60 * 1000;

export function DataExportSection() {
  const latest = useDataExportLatest();
  const list = useDataExportList();
  const request = useRequestDataExport();
  const [activePollId, setActivePollId] = useState<string | null>(null);

  // Активный polling — для последнего in-progress'а ИЛИ только что созданного.
  const last = latest.data;
  const lastIsActive =
    last && (last.status === 'pending' || last.status === 'processing');
  const pollId = activePollId ?? (lastIsActive ? last.id : null);
  const polling = useDataExportStatus(pollId);

  const lastIsRecent =
    last && Date.now() - new Date(last.requested_at).getTime() < COOLDOWN_MS;
  const cooldownLeftMs =
    lastIsRecent && last
      ? Math.max(0, COOLDOWN_MS - (Date.now() - new Date(last.requested_at).getTime()))
      : 0;
  const onCooldown = cooldownLeftMs > 0;

  // current = свежие данные из polling если они есть, иначе — latest.
  const current: DataExportRequest | null = polling.data ?? last ?? null;
  const downloadReady = current?.status === 'ready' && current.download_url;
  const isFailed = current?.status === 'failed';

  async function handleRequest() {
    try {
      const created = await request.mutateAsync();
      setActivePollId(created.id);
      toast.success(
        'Запрос принят. Ссылка появится здесь и придёт на email через несколько минут.',
      );
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'rate_limit') {
        toast.warning('Запрос за последние 24 часа уже есть. Проверь email или подожди.');
        return;
      }
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось создать запрос. Попробуй позже.';
      toast.error(message);
    }
  }

  return (
    <Card>
      <CardTitle>Экспорт моих данных</CardTitle>
      <CardDescription className="mt-1">
        Запросим архив со всеми твоими данными в Brikko: профиль, ключи, транзакции,
        история запросов, настройки. Право портативности по 152-ФЗ ст. 14.
      </CardDescription>

      <div className="mt-4 flex flex-col gap-3">
        {/* Активный или недавно завершённый запрос */}
        {current ? (
          <ActiveRequestCard
            current={current}
            isPolling={polling.isFetching || Boolean(lastIsActive)}
            isFailed={Boolean(isFailed)}
            downloadReady={Boolean(downloadReady)}
          />
        ) : null}

        {/* Cooldown indicator (вне ActiveRequestCard, чтобы был и при terminal-статусе) */}
        {onCooldown && current?.status !== 'pending' && current?.status !== 'processing' ? (
          <CooldownHint cooldownLeftMs={cooldownLeftMs} />
        ) : null}

        <Button
          variant="secondary"
          leftIcon={<Download className="h-4 w-4" />}
          loading={request.isPending}
          disabled={onCooldown || request.isPending}
          onClick={handleRequest}
          className="self-start"
          data-testid="data-export-request-button"
        >
          {lastIsRecent ? 'Запросить ещё раз' : 'Скачать все мои данные'}
        </Button>
      </div>

      {/* Past exports list */}
      <PastExportsList items={list.data ?? []} />
    </Card>
  );
}

function ActiveRequestCard({
  current,
  isPolling,
  isFailed,
  downloadReady,
}: {
  current: DataExportRequest;
  isPolling: boolean;
  isFailed: boolean;
  downloadReady: boolean;
}) {
  const isProcessing = current.status === 'pending' || current.status === 'processing';
  const isExpired = current.status === 'expired';

  return (
    <div
      className="flex items-start gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-body-sm text-gray-700"
      data-testid="data-export-active-card"
    >
      <StatusIcon status={current.status} spinning={isProcessing && isPolling} />
      <div className="flex flex-1 flex-col gap-1">
        <span>
          Запрошено {formatDateTime(current.requested_at)}.{' '}
          <StatusLabel status={current.status} />
        </span>

        {downloadReady && current.download_url ? (
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <a
              href={current.download_url}
              className="inline-flex items-center gap-1 text-brand-600 hover:underline"
              target="_blank"
              rel="noopener noreferrer"
              data-testid="data-export-download-link"
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              Скачать архив
            </a>
            <span className="inline-flex items-center gap-1 text-xs text-gray-500">
              <Mail className="h-3.5 w-3.5" aria-hidden="true" />
              Ссылка также отправлена на email
            </span>
          </div>
        ) : null}

        {isExpired ? (
          <span
            className="mt-1 inline-flex items-center gap-1 text-xs text-gray-500"
            data-testid="data-export-expired-hint"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            Ссылка истекла. Запроси заново.
          </span>
        ) : null}

        {isFailed ? (
          <span
            className="mt-1 inline-flex items-center gap-1 text-xs text-error-600"
            data-testid="data-export-failed-hint"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {current.error_message ?? 'Не удалось собрать архив. Попробуй ещё раз через час.'}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function CooldownHint({ cooldownLeftMs }: { cooldownLeftMs: number }) {
  const hours = Math.ceil(cooldownLeftMs / (60 * 60 * 1000));
  return (
    <div
      className="flex items-start gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600"
      data-testid="data-export-cooldown-hint"
    >
      <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-500" aria-hidden="true" />
      <span>
        Следующий запрос можно сделать через ~{hours}{' '}
        {pluralizeHours(hours)}. Лимит — один экспорт в 24 часа.
      </span>
    </div>
  );
}

function PastExportsList({ items }: { items: DataExportRequest[] }) {
  if (items.length === 0) return null;
  // Top-5 — для compact-view. В V2 добавим «показать все».
  const top = items.slice(0, 5);
  return (
    <div className="mt-6 border-t border-gray-200 pt-4">
      <h4 className="text-body-sm font-medium text-gray-700">История экспортов</h4>
      <ul className="mt-2 flex flex-col gap-2" data-testid="data-export-list">
        {top.map((item) => (
          <li
            key={item.id}
            className="flex items-center justify-between gap-3 text-body-sm"
            data-testid={`data-export-list-item-${item.status}`}
          >
            <span className="text-gray-700">{formatDateTime(item.requested_at)}</span>
            <div className="flex items-center gap-3">
              <StatusBadge status={item.status} />
              {item.status === 'ready' && item.download_url ? (
                <a
                  href={item.download_url}
                  className="text-xs text-brand-600 hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Скачать
                </a>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusIcon({
  status,
  spinning,
}: {
  status: DataExportStatus;
  spinning: boolean;
}) {
  if (spinning) {
    return (
      <Loader2
        className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-brand-600"
        aria-hidden="true"
      />
    );
  }
  if (status === 'ready') {
    return (
      <Check className="mt-0.5 h-4 w-4 shrink-0 text-success-600" aria-hidden="true" />
    );
  }
  if (status === 'failed') {
    return (
      <AlertTriangle
        className="mt-0.5 h-4 w-4 shrink-0 text-error-600"
        aria-hidden="true"
      />
    );
  }
  return <Clock className="mt-0.5 h-4 w-4 shrink-0 text-gray-500" aria-hidden="true" />;
}

function StatusLabel({ status }: { status: DataExportStatus }) {
  const map: Record<DataExportStatus, string> = {
    pending: 'В очереди.',
    processing: 'Готовим архив…',
    ready: 'Архив готов:',
    failed: 'Ошибка при сборке.',
    expired: 'Ссылка истекла.',
  };
  return <>{map[status]}</>;
}

function StatusBadge({ status }: { status: DataExportStatus }) {
  const tone: Record<
    DataExportStatus,
    { variant: 'neutral' | 'brand' | 'success' | 'error' | 'warning'; label: string }
  > = {
    pending: { variant: 'neutral', label: 'В очереди' },
    processing: { variant: 'brand', label: 'Готовим' },
    ready: { variant: 'success', label: 'Готов' },
    failed: { variant: 'error', label: 'Ошибка' },
    expired: { variant: 'warning', label: 'Истёк' },
  };
  const t = tone[status];
  return <Badge variant={t.variant}>{t.label}</Badge>;
}

function pluralizeHours(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'час';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'часа';
  return 'часов';
}
