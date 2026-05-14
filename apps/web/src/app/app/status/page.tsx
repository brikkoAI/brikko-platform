'use client';

import { Activity, AlertCircle, BarChart3, CheckCircle2, Database, ExternalLink, GitCommit, Server, Users, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useAdminStatus } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';
import { toKopecks } from '@/lib/types';

/**
 * Single-pane admin dashboard для CEO (Sprint 13.7, 2026-05-09).
 *
 * Доступ: только email из ADMIN_EMAILS (env-список).  Остальные → 403,
 * показываем "Доступ запрещён".  Не путать с Account.SeatRole.ADMIN —
 * там роль внутри team-аккаунта, тут платформенный CEO-уровень.
 *
 * Что видно:
 *   - Состояние API (большой светофор: ok / degraded / down)
 *   - Last 24h: запросы / ошибки / latency / стоимость / активные аккаунты
 *   - Все провайдеры с pill'ами configured/unconfigured
 *   - DB stats: total accounts / users / traces (24h vs all time)
 *   - Deploy info: sha, version, время деплоя
 *
 * Refetch каждые 30 секунд — CEO открыл страницу и видит свежее.
 */

export default function AdminStatusPage() {
  const status = useAdminStatus();

  if (status.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  if (status.isError) {
    const err = status.error as Error & { status?: number };
    if (err.status === 403) {
      return (
        <Card className="p-12 text-center">
          <XCircle className="mx-auto mb-4 h-12 w-12 text-red-500" aria-hidden="true" />
          <CardTitle className="mb-2 text-h3">Доступ запрещён</CardTitle>
          <CardDescription className="text-body text-fg-muted">
            Эта страница доступна только администраторам платформы. Если вам нужен
            доступ — добавьте email в ADMIN_EMAILS на сервере.
          </CardDescription>
        </Card>
      );
    }
    return (
      <Card className="p-8 text-center">
        <AlertCircle className="mx-auto mb-4 h-10 w-10 text-amber-500" aria-hidden="true" />
        <CardTitle className="mb-2 text-h4">Не удалось загрузить статус</CardTitle>
        <CardDescription>{err.message ?? 'Неизвестная ошибка'}</CardDescription>
      </Card>
    );
  }

  const data = status.data!;
  // Светофор: иконка цветная (всегда хорошо видна), фон — тёмный нейтральный
  // (bg-elevated работает в обеих темах), текст — fg-primary.  Раньше был
  // bg-green-50 + text-fg-primary → в тёмной теме белый на бледно-зелёном
  // = нечитаемо.  Сейчас текст всегда читается, цвет передан через иконку
  // и тонкую цветную полосу слева.
  const accent =
    data.api_status === 'ok'
      ? 'text-green-500 border-l-green-500'
      : data.api_status === 'degraded'
        ? 'text-amber-500 border-l-amber-500'
        : 'text-red-500 border-l-red-500';

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <span className="inline-flex items-center gap-2 text-body-sm text-fg-muted">
          <Activity className="h-4 w-4" aria-hidden="true" />
          Platform · Single-pane dashboard
        </span>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-2">
            <h1 className="text-h2 font-semibold tracking-tight text-fg-primary">
              Статус платформы
            </h1>
            <p className="max-w-2xl text-body text-fg-muted">
              Реальное состояние Brikko Gateway: API, провайдеры, БД, последние
              24 часа трафика. Обновляется автоматически каждые 30 секунд.
            </p>
          </div>
          <a href="/grafana/" target="_blank" rel="noopener noreferrer">
            <Button variant="ghost" className="gap-2">
              <BarChart3 className="h-4 w-4" aria-hidden="true" />
              Открыть Grafana
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </a>
        </div>
      </header>

      {/* Главный светофор */}
      <Card className={`p-8 border-l-4 ${accent}`}>
        <div className="flex items-center gap-4">
          {data.api_status === 'ok' ? (
            <CheckCircle2 className="h-14 w-14 text-green-500" aria-hidden="true" />
          ) : data.api_status === 'degraded' ? (
            <AlertCircle className="h-14 w-14 text-amber-500" aria-hidden="true" />
          ) : (
            <XCircle className="h-14 w-14 text-red-500" aria-hidden="true" />
          )}
          <div>
            <div className="text-h2 font-semibold text-fg-primary">
              {data.api_status === 'ok'
                ? 'Всё работает'
                : data.api_status === 'degraded'
                  ? 'Деградация'
                  : 'API недоступен'}
            </div>
            <div className="mt-1 text-body-sm text-fg-muted">
              Последняя проверка:{' '}
              {new Date(data.timestamp).toLocaleTimeString('ru-RU')}
            </div>
          </div>
        </div>
      </Card>

      {/* Last 24h KPI */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Запросов 24ч"
          value={data.last_24h.requests.toLocaleString('ru-RU')}
          hint={
            data.last_24h.errors > 0
              ? `${data.last_24h.errors} с ошибкой`
              : 'все успешные'
          }
          icon={<Activity className="h-4 w-4" aria-hidden="true" />}
        />
        <KpiCard
          label="Стоимость 24ч"
          value={formatKopecks(toKopecks(data.last_24h.cost_kop), {
            forceFraction: true,
          })}
          icon={<Database className="h-4 w-4" aria-hidden="true" />}
        />
        <KpiCard
          label="Avg latency"
          value={`${data.last_24h.avg_latency_ms.toLocaleString('ru-RU')} мс`}
          icon={<Server className="h-4 w-4" aria-hidden="true" />}
        />
        <KpiCard
          label="Аккаунтов 24ч"
          value={data.last_24h.active_accounts.toLocaleString('ru-RU')}
          hint={`из ${data.database.accounts_total.toLocaleString('ru-RU')} всего`}
          icon={<Users className="h-4 w-4" aria-hidden="true" />}
        />
      </div>

      {/* Provider grid */}
      <Card>
        <div className="border-b border-gray-200 px-5 py-3">
          <CardTitle className="text-body font-medium text-fg-primary">
            Провайдеры моделей
          </CardTitle>
        </div>
        <div className="grid grid-cols-2 gap-2 p-5 sm:grid-cols-3 lg:grid-cols-4">
          {data.providers.map((p) => (
            <ProviderTile key={p.name} provider={p} />
          ))}
        </div>
      </Card>

      {/* DB + Deploy в две колонки */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <CardTitle className="mb-3 text-body font-medium text-fg-primary">
            База данных
          </CardTitle>
          <dl className="grid grid-cols-2 gap-y-2 text-body-sm">
            <dt className="text-fg-muted">Аккаунтов</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.database.accounts_total.toLocaleString('ru-RU')}
            </dd>
            <dt className="text-fg-muted">Пользователей</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.database.users_total.toLocaleString('ru-RU')}
            </dd>
            <dt className="text-fg-muted">Trace-логов всего</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.database.traces_total.toLocaleString('ru-RU')}
            </dd>
            <dt className="text-fg-muted">Trace-логов 24ч</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.database.traces_24h.toLocaleString('ru-RU')}
            </dd>
          </dl>
        </Card>

        <Card className="p-5">
          <CardTitle className="mb-3 inline-flex items-center gap-2 text-body font-medium text-fg-primary">
            <GitCommit className="h-4 w-4" aria-hidden="true" />
            Деплой
          </CardTitle>
          <dl className="grid grid-cols-2 gap-y-2 text-body-sm">
            <dt className="text-fg-muted">Версия</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.deploy.version}
            </dd>
            <dt className="text-fg-muted">Commit</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.deploy.sha ?? '—'}
            </dd>
            <dt className="text-fg-muted">Развёрнут</dt>
            <dd className="text-right font-mono text-fg-primary">
              {data.deploy.deployed_at
                ? new Date(data.deploy.deployed_at).toLocaleString('ru-RU')
                : '—'}
            </dd>
          </dl>
        </Card>
      </div>

      <CardDescription className="px-1 text-body-sm text-fg-faint">
        Эта страница — быстрый daily-check в любом браузере. Для глубоких графиков
        и метрик хоста — кнопка «Открыть Grafana» в шапке (выделено login-паролем).
      </CardDescription>
    </div>
  );
}

function KpiCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: React.ReactNode;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 text-body-sm text-fg-muted">
        {icon}
        {label}
      </div>
      <div className="mt-2 text-h3 font-semibold tracking-tight text-fg-primary">
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-body-sm text-fg-faint">{hint}</div>
      ) : null}
    </Card>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  deepseek: 'DeepSeek',
  yandex: 'YandexGPT',
  sber: 'GigaChat',
};

function ProviderTile({
  provider,
}: {
  provider: { name: string; configured: boolean; status: string };
}) {
  const label = PROVIDER_LABEL[provider.name] ?? provider.name;
  return (
    <div className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2">
      <span className="font-medium text-fg-primary">{label}</span>
      {provider.configured ? (
        <Badge variant="success" className="gap-1">
          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
          ok
        </Badge>
      ) : (
        <Badge variant="neutral">off</Badge>
      )}
    </div>
  );
}
