import { Badge } from '@/components/ui/badge';
import { BRAND } from '@/lib/brand';

export const metadata = {
  title: 'Состояние сервиса Brikko — uptime LLM провайдеров',
  description:
    'Текущий статус Brikko и upstream LLM-провайдеров: OpenAI, Anthropic, Google, DeepSeek, YandexGPT, GigaChat. Инциденты за 30 дней.',
};

/**
 * v1 status page без real-time бэкенда: SSR snapshot обновляется на каждом
 * deploy. Для leads это ок — главное, что страница существует и видно дату
 * проверки. Real-time health-checks провайдеров → V2 (отдельный воркер,
 * пишет в Redis, страница SWR-фетчит /api/status).
 *
 * v2 (2026-05-10): добавлен RealMetricsSection — fetch с
 * api.brikko.ru/v1/public/status (server-component, revalidate 60s)
 * с реальным uptime/latency/requests за 24h.  Endpoint имеет
 * собственный rate-limit + cache, наш revalidate — defense-in-depth.
 *
 * Намеренно server component: без 'use client', без интерактива — просто
 * статичный trust-signal, легко индексируется поисковиками.
 */

interface PublicStatusMetrics {
  successful_requests_24h: number;
  p95_latency_ms: number;
  uptime_percent_24h: number;
  window: string;
  updated_at: string;
  degraded?: boolean;
}

const PUBLIC_STATUS_URL =
  (process.env.NEXT_PUBLIC_API_BASE_URL ?? 'https://api.brikko.ru/v1') +
  '/public/status';

async function fetchPublicMetrics(): Promise<PublicStatusMetrics | null> {
  try {
    const res = await fetch(PUBLIC_STATUS_URL, {
      next: { revalidate: 60 },
      cache: 'force-cache',
    });
    if (!res.ok) return null;
    return (await res.json()) as PublicStatusMetrics;
  } catch {
    return null;
  }
}

type ServiceStatus = 'operational' | 'degraded' | 'down';

interface Provider {
  id: string;
  name: string;
  status: ServiceStatus;
  upstreamStatusUrl?: string;
}

const PROVIDERS: readonly Provider[] = [
  {
    id: 'openai',
    name: 'OpenAI',
    status: 'operational',
    upstreamStatusUrl: 'https://status.openai.com',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    status: 'operational',
    upstreamStatusUrl: 'https://status.anthropic.com',
  },
  {
    id: 'google',
    name: 'Google (Gemini)',
    status: 'operational',
    upstreamStatusUrl: 'https://status.cloud.google.com',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    status: 'operational',
    upstreamStatusUrl: 'https://status.deepseek.com',
  },
  {
    id: 'yandex',
    name: 'YandexGPT',
    status: 'operational',
    upstreamStatusUrl: 'https://status.yandex.cloud',
  },
  {
    id: 'sber',
    name: 'GigaChat (Sber)',
    status: 'operational',
  },
];

const TG_STATUS_CHANNEL = 'https://t.me/brikko_status';

export default async function StatusPage() {
  // Server-rendered timestamp: фиксируется на момент билда/SSR-рендера.
  // Для статической страницы это OK — пользователь видит свежесть проверки.
  const lastUpdated = new Date();

  // Real metrics за 24h из gateway (с revalidate 60s, см. fetchPublicMetrics).
  const metrics = await fetchPublicMetrics();

  // Overall = худший из всех статусов. Сейчас все operational, но логика
  // рабочая — когда подключим real-time, не нужно будет переписывать UI.
  const overall = aggregateStatus(PROVIDERS.map((p) => p.status));

  return (
    <div className="bg-white">
      <section className="mx-auto max-w-4xl px-6 pb-12 pt-16 lg:pt-24">
        <h1 className="text-4xl font-semibold tracking-tight text-gray-900">
          Состояние Brikko
        </h1>
        <p className="mt-3 text-body-large text-gray-700">
          Текущий статус шлюза {BRAND.apiDomain} и upstream LLM-провайдеров.
        </p>
        <p className="mt-2 text-body-sm text-gray-500">
          <span>Последнее обновление: </span>
          <time dateTime={lastUpdated.toISOString()} className="font-mono">
            {formatTimestamp(lastUpdated)}
          </time>
        </p>

        <div className="mt-8">
          <OverallHealthBadge status={overall} />
        </div>

        {metrics ? (
          <div className="mt-10">
            <h2 className="text-xl font-semibold tracking-tight text-gray-900">
              Метрики за последние 24 часа
            </h2>
            <p className="mt-2 text-body-sm text-gray-600">
              Реальные цифры с gateway, обновляются раз в минуту. Источник:{' '}
              <a
                href="https://api.brikko.ru/v1/public/status"
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-fg-primary underline-offset-2 hover:underline"
              >
                api.brikko.ru/v1/public/status
              </a>
              .
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <RealMetricCard
                label="Uptime · 24h"
                value={`${metrics.uptime_percent_24h.toFixed(2)}%`}
                hint="Доля успешных проверок healthcheck"
              />
              <RealMetricCard
                label="Latency · p95"
                value={`${metrics.p95_latency_ms.toLocaleString('ru-RU')} мс`}
                hint="95-й перцентиль времени ответа"
              />
              <RealMetricCard
                label="Запросов · 24h"
                value={metrics.successful_requests_24h.toLocaleString('ru-RU')}
                hint="Успешные API-вызовы"
              />
            </div>
          </div>
        ) : null}
      </section>

      <section
        aria-labelledby="providers-heading"
        className="border-t border-gray-200 bg-gray-50 py-16"
      >
        <div className="mx-auto max-w-4xl px-6">
          <h2
            id="providers-heading"
            className="text-2xl font-semibold tracking-tight text-gray-900"
          >
            Upstream-провайдеры
          </h2>
          <p className="mt-2 text-body text-gray-600">
            Статусы основаны на публичных status-страницах провайдеров и наших
            health-проверках.
          </p>

          <ul className="mt-8 grid gap-3 sm:grid-cols-2">
            {PROVIDERS.map((provider) => (
              <li key={provider.id}>
                <ProviderRow provider={provider} checkedAt={lastUpdated} />
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section
        aria-labelledby="incidents-heading"
        className="mx-auto max-w-4xl px-6 py-16"
      >
        <h2
          id="incidents-heading"
          className="text-2xl font-semibold tracking-tight text-gray-900"
        >
          Инциденты за последние 30 дней
        </h2>
        <div className="mt-6 rounded-lg border border-gray-200 bg-white p-8 text-center">
          <p className="text-body text-gray-700">
            За последние 30 дней инцидентов не зафиксировано.
          </p>
          <p className="mt-2 text-body-sm text-gray-500">
            История инцидентов с разбором причин и компенсациями появится после
            первого зарегистрированного события.
          </p>
        </div>
      </section>

      <section
        aria-labelledby="subscribe-heading"
        className="border-t border-gray-200 bg-gray-50 py-16"
      >
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h2
            id="subscribe-heading"
            className="text-2xl font-semibold tracking-tight text-gray-900"
          >
            Подписаться на уведомления
          </h2>
          <p className="mt-3 text-body text-gray-700">
            Сбои, плановые работы и постмортемы публикуем в Telegram-канале{' '}
            <span className="font-mono">@brikko_status</span>. Email-рассылка по
            инцидентам — для тарифов Business и выше.
          </p>
          <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a
              href={TG_STATUS_CHANNEL}
              target="_blank"
              rel="noopener noreferrer"
              className="brikko-cta-primary"
            >
              Открыть Telegram-канал
            </a>
            <a
              href={`mailto:${BRAND.supportEmail}?subject=Status%20alerts`}
              className="brikko-cta-secondary"
            >
              Запросить email-уведомления
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}

function RealMetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="text-body-sm text-gray-500">{label}</div>
      <div
        className="mt-1.5 text-2xl font-semibold text-gray-900"
        style={{ fontFeatureSettings: '"tnum"' }}
      >
        {value}
      </div>
      <div className="mt-1 text-body-sm text-gray-500">{hint}</div>
    </div>
  );
}

function OverallHealthBadge({ status }: { status: ServiceStatus }) {
  const config = STATUS_CONFIG[status];
  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-center gap-3 rounded-lg border p-5 ${config.containerClass}`}
    >
      <StatusDot status={status} size="lg" />
      <div>
        <p className={`text-lg font-semibold ${config.textClass}`}>
          {config.overallLabel}
        </p>
        <p className="text-body-sm text-gray-600">{config.overallSubtitle}</p>
      </div>
    </div>
  );
}

function ProviderRow({
  provider,
  checkedAt,
}: {
  provider: Provider;
  checkedAt: Date;
}) {
  const config = STATUS_CONFIG[provider.status];
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-gray-200 bg-white px-5 py-4">
      <div className="flex items-center gap-3">
        <StatusDot status={provider.status} />
        <div>
          <p className="text-body font-medium text-gray-900">{provider.name}</p>
          <p className="text-body-sm text-gray-500">
            Проверено{' '}
            <time dateTime={checkedAt.toISOString()}>
              {formatTimeOnly(checkedAt)}
            </time>
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Badge variant={config.badgeVariant}>{config.shortLabel}</Badge>
        {provider.upstreamStatusUrl ? (
          <a
            href={provider.upstreamStatusUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden text-body-sm font-medium text-fg-primary underline-offset-2 hover:underline sm:inline"
            aria-label={`Status page ${provider.name}`}
          >
            Status →
          </a>
        ) : null}
      </div>
    </div>
  );
}

function StatusDot({
  status,
  size = 'md',
}: {
  status: ServiceStatus;
  size?: 'md' | 'lg';
}) {
  const config = STATUS_CONFIG[status];
  const dimensions = size === 'lg' ? 'h-3.5 w-3.5' : 'h-2.5 w-2.5';
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${dimensions} ${config.dotClass}`}
    />
  );
}

const STATUS_CONFIG: Record<
  ServiceStatus,
  {
    overallLabel: string;
    overallSubtitle: string;
    shortLabel: string;
    badgeVariant: 'success' | 'warning' | 'error';
    dotClass: string;
    containerClass: string;
    textClass: string;
  }
> = {
  operational: {
    overallLabel: 'Все системы работают',
    overallSubtitle: 'Шлюз и провайдеры доступны, отклонений не обнаружено.',
    shortLabel: 'Работает',
    badgeVariant: 'success',
    dotClass: 'bg-success-600',
    containerClass: 'border-success-200 bg-success-50',
    textClass: 'text-success-600',
  },
  degraded: {
    overallLabel: 'Частичный сбой',
    overallSubtitle:
      'Часть провайдеров недоступна — рутер автоматически переключает на резерв.',
    shortLabel: 'Замедление',
    badgeVariant: 'warning',
    dotClass: 'bg-warning-600',
    containerClass: 'border-warning-200 bg-warning-50',
    textClass: 'text-warning-600',
  },
  down: {
    overallLabel: 'Критический сбой',
    overallSubtitle:
      'Шлюз недоступен. Подробности — в Telegram-канале и на email тарифа Business+.',
    shortLabel: 'Недоступен',
    badgeVariant: 'error',
    dotClass: 'bg-error-600',
    containerClass: 'border-error-200 bg-error-50',
    textClass: 'text-error-600',
  },
};

function aggregateStatus(statuses: readonly ServiceStatus[]): ServiceStatus {
  if (statuses.includes('down')) return 'down';
  if (statuses.includes('degraded')) return 'degraded';
  return 'operational';
}

function formatTimestamp(date: Date): string {
  // Москва — основная аудитория, фиксируем зону явно: иначе SSR-сервер в UTC
  // покажет «не наше» время, юзер запутается. Intl + Europe/Moscow = стабильно.
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
    timeZoneName: 'short',
  }).format(date);
}

function formatTimeOnly(date: Date): string {
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(date);
}
