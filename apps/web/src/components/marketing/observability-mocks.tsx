/**
 * Observability mock-views — Cream Studio v6 (Sprint 13.8 landing refresh,
 * 2026-05-09).
 *
 * Pure-presentational компоненты с захардкоженными фикстурами, которые
 * рисуют /app/analytics, /app/traces и /app/status «как у клиента».
 * Используются дважды:
 *   1. Внутри ObservabilityShowcase — в browser-chrome рамках на главной.
 *   2. На страницах /demo/{traces,analytics,status} — full-screen без рамки.
 *
 * Почему отдельный файл, а не рефактор apps/web/src/app/app/X/page.tsx:
 * существующие страницы плотно завязаны на useTraces / useAnalyticsSummary
 * / useAdminStatus хуки и react-query store. Вынос pure-view-компонента
 * требовал бы пересмотра pagination/filter-state и risk регрессий в
 * работающем BrikkoLens (Sprint 1в/2 уже live). Маркетинговый mock-up
 * обходится без фильтров и интеракций — здесь они только декорация.
 *
 * Цветовой бюджет (Cream Studio v6):
 *   - 1 зелёный пиксель: светофор «All systems operational» в /demo/status.
 *   - 1-2 accent-1 пятна: один rate-limited бейдж в traces, последний bar в
 *     bar-chart analytics. Остальное — strict grayscale.
 *   - НЕТ gradient-ов, НЕТ цветных KPI карточек.
 */

import { Activity, AlertCircle, BarChart3, CheckCircle2, Database, Hash, Layers, Server, Users } from 'lucide-react';

// ============================================================
// ANALYTICS MOCK
// ============================================================

const ANALYTICS_KPIS = [
  { label: 'Запросы', value: '47 328', icon: 'activity' as const, hint: '+18% к пред. неделе' },
  { label: 'Стоимость', value: '284,50 ₽', icon: 'layers' as const, hint: '12,4M токенов' },
  { label: 'Avg latency', value: '1 240 мс', icon: 'server' as const, hint: 'p95 3 120 мс' },
  { label: 'Ошибки', value: '0,3%', icon: 'alert' as const, hint: '142 запроса' },
];

const ANALYTICS_DAILY = [
  { day: 'Пн', value: 4_120 },
  { day: 'Вт', value: 5_240 },
  { day: 'Ср', value: 5_910 },
  { day: 'Чт', value: 6_280 },
  { day: 'Пт', value: 7_840 },
  { day: 'Сб', value: 8_120 },
  { day: 'Вс', value: 9_818 },
];

const ANALYTICS_MODELS = [
  { key: 'gpt-4o', share: 0.62, requests: 29_343, cost: '176,40 ₽' },
  { key: 'claude-3.5-sonnet', share: 0.28, requests: 13_252, cost: '79,60 ₽' },
  { key: 'gemini-1.5-pro', share: 0.10, requests: 4_733, cost: '28,50 ₽' },
];

function KpiIcon({ which }: { which: 'activity' | 'layers' | 'server' | 'alert' }) {
  const props = { className: 'h-4 w-4', 'aria-hidden': true } as const;
  if (which === 'activity') return <Activity {...props} />;
  if (which === 'layers') return <Layers {...props} />;
  if (which === 'server') return <Server {...props} />;
  return <AlertCircle {...props} />;
}

export function AnalyticsMockView({ compact = false }: { compact?: boolean }) {
  const maxBar = Math.max(...ANALYTICS_DAILY.map((d) => d.value));
  return (
    <div className={compact ? 'flex flex-col gap-4 p-4' : 'flex flex-col gap-6 p-6'}>
      <header className="flex flex-col gap-1">
        <span className="inline-flex items-center gap-2 text-[12px] uppercase tracking-[0.18em] text-[color:var(--fg-faint)]">
          <BarChart3 className="h-3.5 w-3.5" aria-hidden="true" />
          BrikkoLens · Аналитика
        </span>
        <h2
          className={
            'font-medium tracking-tight text-[color:var(--fg-primary)] ' +
            (compact ? 'text-[20px]' : 'text-[28px]')
          }
        >
          Последние 7 дней
        </h2>
      </header>

      <div
        className={
          // Compact mode (на лендинге внутри featured-карточки) — всегда 2×2,
          // даже на desktop. На demo-странице (compact=false) растягивается
          // в 1×4 на больших экранах. Это решает проблему «текст обрезается»
          // в featured-моке шириной ~700px на desktop и ≤400px на mobile.
          compact
            ? 'grid grid-cols-2 gap-2.5'
            : 'grid grid-cols-2 gap-3 lg:grid-cols-4'
        }
      >
        {ANALYTICS_KPIS.map((k) => (
          <div
            key={k.label}
            className={
              'rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] ' +
              (compact ? 'p-3' : 'p-4')
            }
          >
            <div className="flex items-center gap-1.5 text-[11px] text-[color:var(--fg-muted)] truncate">
              <KpiIcon which={k.icon} />
              <span className="truncate">{k.label}</span>
            </div>
            <div
              className={
                'mt-1.5 font-semibold tracking-tight text-[color:var(--fg-primary)] tabular-nums truncate ' +
                (compact ? 'text-[16px]' : 'text-[22px]')
              }
            >
              {k.value}
            </div>
            {!compact ? (
              <div className="mt-0.5 text-[12px] text-[color:var(--fg-faint)] truncate">
                {k.hint}
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4">
        <div className="flex items-center justify-between border-b border-[color:var(--hairline)] pb-3">
          <span className="text-[13px] font-medium text-[color:var(--fg-primary)]">
            Запросы по дням
          </span>
          <span className="text-[12px] text-[color:var(--fg-faint)]">2026-05-03 — 2026-05-09</span>
        </div>
        <div className={`flex items-end gap-2 pt-4 ${compact ? 'h-28' : 'h-40'}`}>
          {ANALYTICS_DAILY.map((d, i) => {
            const isLast = i === ANALYTICS_DAILY.length - 1;
            const heightPct = (d.value / maxBar) * 100;
            return (
              <div key={d.day} className="flex flex-1 flex-col items-center gap-1">
                <div
                  className="w-full rounded-sm transition-colors"
                  style={{
                    height: `${heightPct}%`,
                    minHeight: 6,
                    // var(--fg-faint) слишком слабо в dark theme — почти
                    // сливается с фоном. fg-muted даёт читаемый контраст.
                    background: isLast ? 'var(--accent-1)' : 'var(--fg-muted)',
                    opacity: isLast ? 1 : 0.7,
                  }}
                  aria-label={`${d.day}: ${d.value.toLocaleString('ru-RU')} запросов`}
                />
                <span className="text-[11px] text-[color:var(--fg-faint)]">{d.day}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4">
        <div className="border-b border-[color:var(--hairline)] pb-3 text-[13px] font-medium text-[color:var(--fg-primary)]">
          Топ моделей
        </div>
        <ul className="flex flex-col gap-3 pt-3">
          {ANALYTICS_MODELS.map((m) => (
            <li key={m.key}>
              <div className="flex items-center justify-between text-[13px]">
                <span className="font-mono text-[color:var(--fg-primary)]">{m.key}</span>
                <span className="text-[color:var(--fg-muted)] tabular-nums">
                  {m.requests.toLocaleString('ru-RU')} · {m.cost} ·{' '}
                  {(m.share * 100).toLocaleString('ru-RU')}%
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--bg-elevated)]">
                <div
                  className="h-full rounded-full bg-[color:var(--fg-primary)]"
                  style={{ width: `${Math.max(2, m.share * 100)}%`, opacity: 0.85 }}
                />
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ============================================================
// TRACES MOCK
// ============================================================

type TraceStatus = 'ok' | 'rate_limited' | 'in_flight';

type TraceRow = {
  time: string;
  model: string;
  provider: string;
  requestId: string;
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
  costRub: string;
  status: TraceStatus;
  flags: ('stream' | 'tools' | 'cache' | 'pii')[];
};

const TRACE_ROWS: TraceRow[] = [
  {
    time: '14:32:08',
    model: 'gpt-4o',
    provider: 'openai',
    requestId: 'req_8a2f1cd9b4',
    promptTokens: 1_842,
    completionTokens: 642,
    latencyMs: 1_410,
    costRub: '0,84 ₽',
    status: 'ok',
    flags: ['stream'],
  },
  {
    time: '14:31:55',
    model: 'claude-3.5-sonnet',
    provider: 'anthropic',
    requestId: 'req_2e5f8b104c',
    promptTokens: 12_410,
    completionTokens: 1_280,
    latencyMs: 2_840,
    costRub: '2,10 ₽',
    status: 'ok',
    flags: ['tools', 'pii'],
  },
  {
    time: '14:31:42',
    model: 'gpt-4o-mini',
    provider: 'openai',
    requestId: 'req_4d09a72e15',
    promptTokens: 612,
    completionTokens: 184,
    latencyMs: 380,
    costRub: '0,03 ₽',
    status: 'ok',
    flags: ['cache'],
  },
  {
    time: '14:31:18',
    model: 'gpt-4o',
    provider: 'openai',
    requestId: 'req_7c1ba934f0',
    promptTokens: 24_120,
    completionTokens: 0,
    latencyMs: 240,
    costRub: '—',
    status: 'rate_limited',
    flags: [],
  },
  {
    time: '14:31:02',
    model: 'gemini-1.5-pro',
    provider: 'google',
    requestId: 'req_a51f6c0d28',
    promptTokens: 3_240,
    completionTokens: 980,
    latencyMs: 1_840,
    costRub: '0,42 ₽',
    status: 'ok',
    flags: ['stream', 'pii'],
  },
  {
    time: '14:30:51',
    model: 'claude-3-haiku',
    provider: 'anthropic',
    requestId: 'req_e9b48317da',
    promptTokens: 542,
    completionTokens: 0,
    latencyMs: 0,
    costRub: '—',
    status: 'in_flight',
    flags: ['stream'],
  },
];

function FlagBadge({ kind }: { kind: 'stream' | 'tools' | 'cache' | 'pii' }) {
  return (
    <span
      className="inline-flex items-center rounded-md border border-[color:var(--hairline)] bg-[color:var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[10px] text-[color:var(--fg-muted)]"
      aria-label={`flag ${kind}`}
    >
      {kind}
    </span>
  );
}

function StatusPill({ status }: { status: TraceStatus }) {
  if (status === 'rate_limited') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium"
        style={{
          background: 'var(--accent-1)',
          color: 'var(--on-accent)',
        }}
      >
        rate-limited
      </span>
    );
  }
  if (status === 'in_flight') {
    return (
      <span
        className="brikko-pulse inline-flex items-center gap-1 rounded-md border border-[color:var(--hairline)] bg-[color:var(--bg-elevated)] px-2 py-0.5 text-[11px] text-[color:var(--fg-muted)]"
        aria-label="запрос в процессе"
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          aria-hidden="true"
          style={{ background: 'var(--fg-muted)' }}
        />
        in-flight
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-[color:var(--hairline)] bg-[color:var(--bg-elevated)] px-2 py-0.5 text-[11px] text-[color:var(--fg-muted)]">
      <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
      ОК
    </span>
  );
}

export function TracesMockView({ compact = false }: { compact?: boolean }) {
  const rows = compact ? TRACE_ROWS.slice(0, 4) : TRACE_ROWS;
  return (
    <div className={compact ? 'flex flex-col gap-3 p-4' : 'flex flex-col gap-4 p-6'}>
      <header className="flex flex-col gap-1">
        <span className="inline-flex items-center gap-2 text-[12px] uppercase tracking-[0.18em] text-[color:var(--fg-faint)]">
          <Activity className="h-3.5 w-3.5" aria-hidden="true" />
          BrikkoLens · Запросы
        </span>
        <h2
          className={
            'font-medium tracking-tight text-[color:var(--fg-primary)] ' +
            (compact ? 'text-[18px]' : 'text-[28px]')
          }
        >
          Последние вызовы
        </h2>
      </header>

      <div className="overflow-hidden rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)]">
        {/* compact: 3 столбца (время / модель / статус) — широкие колонки
             cost / latency / tokens идут второй строкой под моделью.  Fixed
             column widths распирали правый контейнер на 500+px и ломали
             8/4-grid лендинга — в compact они должны быть auto-shrink. */}
        <div
          className={
            'grid items-center border-b border-[color:var(--hairline)] bg-[color:var(--bg-elevated)] px-4 py-2 text-[11px] uppercase tracking-wider text-[color:var(--fg-faint)] ' +
            (compact
              ? 'grid-cols-[64px_1fr_88px] gap-2'
              : 'grid-cols-[80px_1fr_120px_90px_90px_120px] gap-3')
          }
        >
          <span>Время</span>
          <span>Модель</span>
          {!compact && <span className="text-right">Токены</span>}
          {!compact && <span className="text-right">Latency</span>}
          {!compact && <span className="text-right">Стоимость</span>}
          <span className={compact ? 'text-right' : ''}>Статус</span>
        </div>
        <ul className="divide-y divide-[color:var(--hairline)]">
          {rows.map((row) => (
            <li
              key={row.requestId}
              className={
                'grid items-center px-4 py-3 text-[13px] ' +
                (compact
                  ? 'grid-cols-[64px_1fr_88px] gap-2'
                  : 'grid-cols-[80px_1fr_120px_90px_90px_120px] gap-3')
              }
            >
              <span className="font-mono text-[color:var(--fg-muted)]">{row.time}</span>
              <span className="flex flex-col gap-0.5">
                <span className="flex items-center gap-2 font-medium text-[color:var(--fg-primary)]">
                  <Server className="h-3.5 w-3.5 text-[color:var(--fg-faint)]" aria-hidden="true" />
                  {row.model}
                </span>
                <span className="flex items-center gap-1 text-[11px] text-[color:var(--fg-faint)]">
                  <Hash className="h-3 w-3" aria-hidden="true" />
                  <span className="font-mono">{row.requestId}</span>
                  {row.flags.map((f) => (
                    <FlagBadge key={f} kind={f} />
                  ))}
                </span>
              </span>
              {!compact && (
                <span className="text-right font-mono tabular-nums text-[color:var(--fg-primary)]">
                  {row.promptTokens.toLocaleString('ru-RU')}
                  <span className="text-[color:var(--fg-faint)]"> + </span>
                  {row.completionTokens.toLocaleString('ru-RU')}
                </span>
              )}
              {!compact && (
                <span className="text-right font-mono tabular-nums text-[color:var(--fg-muted)]">
                  {row.status === 'in_flight' ? '—' : `${row.latencyMs.toLocaleString('ru-RU')} мс`}
                </span>
              )}
              {!compact && (
                <span className="text-right font-mono tabular-nums text-[color:var(--fg-primary)]">
                  {row.costRub}
                </span>
              )}
              <span className={compact ? 'flex justify-end' : ''}>
                <StatusPill status={row.status} />
              </span>
            </li>
          ))}
        </ul>
      </div>

      <style>{`
        @keyframes brikkoPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
        .brikko-pulse {
          animation: brikkoPulse 1.6s var(--ease-in-out-quart) infinite;
        }
      `}</style>
    </div>
  );
}

// ============================================================
// STATUS MOCK
// ============================================================

const STATUS_KPIS = [
  { label: 'Uptime', value: '99,97%', hint: '30 дней' },
  { label: 'Latency p95', value: '412 мс', hint: 'все модели' },
  { label: 'Error rate', value: '0,03%', hint: 'последний час' },
  { label: 'Запросов/мин', value: '142', hint: 'live' },
];

type ProviderHealth = {
  name: string;
  state: 'ok' | 'degraded';
};

const STATUS_PROVIDERS: ProviderHealth[] = [
  { name: 'OpenAI', state: 'ok' },
  { name: 'Anthropic', state: 'ok' },
  { name: 'Google', state: 'ok' },
  { name: 'DeepSeek', state: 'degraded' },
  { name: 'YandexGPT', state: 'ok' },
  { name: 'GigaChat', state: 'ok' },
];

export function StatusMockView({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? 'flex flex-col gap-4 p-4' : 'flex flex-col gap-6 p-6'}>
      <header className="flex flex-col gap-1">
        <span className="inline-flex items-center gap-2 text-[12px] uppercase tracking-[0.18em] text-[color:var(--fg-faint)]">
          <Activity className="h-3.5 w-3.5" aria-hidden="true" />
          Platform · Status
        </span>
        <h2
          className={
            'font-medium tracking-tight text-[color:var(--fg-primary)] ' +
            (compact ? 'text-[18px]' : 'text-[28px]')
          }
        >
          Состояние Brikko Gateway
        </h2>
      </header>

      <div
        className="flex items-center gap-3 rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4"
        style={{ borderLeft: '3px solid #16a34a' }}
      >
        <CheckCircle2 className="h-9 w-9 flex-shrink-0" style={{ color: '#16a34a' }} aria-hidden="true" />
        <div>
          <div
            className={
              'font-semibold tracking-tight text-[color:var(--fg-primary)] ' +
              (compact ? 'text-[16px]' : 'text-[20px]')
            }
          >
            All systems operational
          </div>
          <div className="text-[12px] text-[color:var(--fg-muted)]">
            Все провайдеры и инфраструктура работают штатно
          </div>
        </div>
      </div>

      <div
        className={
          compact
            ? 'grid grid-cols-2 gap-2.5'
            : 'grid grid-cols-2 gap-3 lg:grid-cols-4'
        }
      >
        {STATUS_KPIS.map((k) => (
          <div
            key={k.label}
            className={
              'rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] ' +
              (compact ? 'p-3' : 'p-4')
            }
          >
            <div className="text-[11px] text-[color:var(--fg-muted)] truncate">{k.label}</div>
            <div
              className={
                'mt-1.5 font-semibold tracking-tight text-[color:var(--fg-primary)] tabular-nums truncate ' +
                (compact ? 'text-[16px]' : 'text-[20px]')
              }
            >
              {k.value}
            </div>
            {!compact ? (
              <div className="mt-0.5 text-[11px] text-[color:var(--fg-faint)] truncate">
                {k.hint}
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4">
        <div className="border-b border-[color:var(--hairline)] pb-3 text-[13px] font-medium text-[color:var(--fg-primary)]">
          Провайдеры моделей
        </div>
        <div className="grid grid-cols-2 gap-2 pt-3 sm:grid-cols-3">
          {STATUS_PROVIDERS.map((p) => {
            const ok = p.state === 'ok';
            return (
              <div
                key={p.name}
                className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-[color:var(--hairline)] px-3 py-2 text-[13px]"
                title={ok ? `${p.name}: ok` : `${p.name}: degraded`}
              >
                <span className="truncate font-medium text-[color:var(--fg-primary)]">
                  {p.name}
                </span>
                <span
                  className="inline-flex flex-shrink-0 items-center gap-1 text-[11px] font-medium tabular-nums"
                  style={{ color: ok ? '#16a34a' : 'var(--fg-faint)' }}
                  aria-label={ok ? `${p.name} ok` : `${p.name} degraded`}
                >
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 rounded-full"
                    style={{ background: ok ? '#16a34a' : 'var(--fg-faint)' }}
                  />
                  {/* В compact — только цветная точка без подписи (не помещается).
                       В full — текст «ok»/«degraded» рядом. */}
                  {!compact && (ok ? 'ok' : 'degraded')}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {!compact ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4">
            <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-[color:var(--fg-primary)]">
              <Database className="h-4 w-4" aria-hidden="true" />
              База данных
            </div>
            <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
              <dt className="text-[color:var(--fg-muted)]">Аккаунтов</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">1 284</dd>
              <dt className="text-[color:var(--fg-muted)]">Пользователей</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">1 542</dd>
              <dt className="text-[color:var(--fg-muted)]">Trace-логов 24ч</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">204 891</dd>
            </dl>
          </div>
          <div className="rounded-2xl border border-[color:var(--hairline)] bg-[color:var(--bg-base)] p-4">
            <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-[color:var(--fg-primary)]">
              <Users className="h-4 w-4" aria-hidden="true" />
              Активные пользователи
            </div>
            <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
              <dt className="text-[color:var(--fg-muted)]">За 24 часа</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">312</dd>
              <dt className="text-[color:var(--fg-muted)]">За 7 дней</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">894</dd>
              <dt className="text-[color:var(--fg-muted)]">Запросов/мин</dt>
              <dd className="text-right font-mono text-[color:var(--fg-primary)]">142</dd>
            </dl>
          </div>
        </div>
      ) : null}
    </div>
  );
}
