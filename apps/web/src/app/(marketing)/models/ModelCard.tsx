import { Activity, Wrench, Braces, Eye, Shield, ShieldCheck, Sparkles } from 'lucide-react';
import { formatRub, formatTokens } from '@/lib/utils';
import type { PublicModel, Tier } from '@/lib/models-fallback';

/**
 * Карточка модели — Cream Studio v6 (Sprint 13, 2026-05-02 rebrand).
 *
 * Server-component (без 'use client'): чистый рендер.
 *
 * UX:
 *   - double-bezel grayscale через CSS-vars (--bg-base / --bg-elevated / --hairline).
 *   - tier-pill в правом верхнем углу — тонкий signal уровня модели.
 *   - capability-icons серой полосой внизу — компактный chip-сигнал «vision/tools/json».
 *   - deprecated_at — приглушённый warning-stripe с датой ухода.
 *
 * Variant `highlighted` — для трёх рекомендаций над каталогом: добавляет
 * accent-1 outline + Sparkles-badge сверху. accent-1 переключается с
 * espresso → light-stone в тёмной теме автоматически.
 */
interface ModelCardProps {
  model: PublicModel;
  variant?: 'default' | 'highlighted';
  highlightLabel?: string;
}

const TIER_LABELS: Record<Tier, string> = {
  NANO: 'Nano',
  BUDGET: 'Budget',
  MID: 'Mid',
  FLAGSHIP: 'Flagship',
  PREMIUM: 'Premium',
};

export function ModelCard({
  model,
  variant = 'default',
  highlightLabel,
}: ModelCardProps) {
  const isDeprecated = Boolean(model.deprecated_at);
  const isHighlighted = variant === 'highlighted';

  return (
    <article
      className="brikko-card-outer"
      style={{
        height: '100%',
        position: 'relative',
        ...(isHighlighted ? { borderColor: 'var(--accent-1)' } : {}),
      }}
      aria-labelledby={`model-${model.id}-title`}
    >
      {highlightLabel ? (
        <div
          style={{
            position: 'absolute',
            top: -10,
            left: 16,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 10px',
            background: 'var(--bg-base)',
            border: '1px solid var(--accent-1)',
            borderRadius: 9999,
            fontSize: 11,
            fontWeight: 500,
            color: 'var(--fg-primary)',
            letterSpacing: '0.04em',
            zIndex: 2,
          }}
        >
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden="true" />
          {highlightLabel}
        </div>
      ) : null}

      <div
        className="brikko-card-inner"
        style={{ padding: 24, display: 'flex', flexDirection: 'column', height: '100%' }}
      >
        <header
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <h3
              id={`model-${model.id}-title`}
              style={{
                fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                fontWeight: 400,
                fontSize: 20,
                lineHeight: 1.2,
                letterSpacing: '-0.01em',
                color: 'var(--fg-primary)',
                margin: 0,
              }}
            >
              {model.display_name}
            </h3>
            <p style={{ marginTop: 4, fontSize: 13, color: 'var(--fg-muted)' }}>
              {model.provider_display_name}
            </p>
          </div>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-end',
              gap: 6,
              flexShrink: 0,
            }}
          >
            <span
              style={{
                padding: '3px 10px',
                border: '1px solid var(--hairline)',
                borderRadius: 9999,
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                fontSize: 9,
                fontWeight: 500,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--fg-muted)',
              }}
            >
              {TIER_LABELS[model.tier]}
            </span>
            <PrivacyV2Pill />
          </div>
        </header>

        {model.description ? (
          <p
            style={{
              marginTop: 12,
              fontSize: 13,
              lineHeight: 1.5,
              color: 'var(--fg-muted)',
            }}
          >
            {model.description}
          </p>
        ) : null}

        <dl
          style={{
            marginTop: 18,
            paddingTop: 14,
            borderTop: '1px solid var(--hairline)',
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            columnGap: 16,
            rowGap: 8,
          }}
        >
          <PriceCell label="Вход" rubPer1m={model.pricing_rub_per_1m.input} />
          <PriceCell label="Выход" rubPer1m={model.pricing_rub_per_1m.output} />
          {model.pricing_rub_per_1m.cached_input !== null ? (
            <PriceCell label="Кеш" rubPer1m={model.pricing_rub_per_1m.cached_input} muted />
          ) : null}
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <dt
              style={{
                fontSize: 10,
                textTransform: 'uppercase',
                letterSpacing: '0.18em',
                color: 'var(--fg-faint)',
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              }}
            >
              Контекст
            </dt>
            <dd
              style={{
                marginTop: 2,
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                fontSize: 13,
                color: 'var(--fg-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {formatContextWindow(model.context_tokens)}
            </dd>
          </div>
        </dl>

        <div style={{ marginTop: 'auto', paddingTop: 18 }}>
          <CapabilityRow capabilities={model.capabilities} />
        </div>

        {isDeprecated && model.deprecated_at ? (
          <p
            style={{
              marginTop: 14,
              padding: '8px 10px',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--hairline)',
              borderRadius: 12,
              fontSize: 11,
              color: 'var(--fg-muted)',
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            }}
          >
            Снимается с поддержки {formatDateRu(model.deprecated_at)}
          </p>
        ) : null}
      </div>
    </article>
  );
}

/**
 * Privacy v2 — все модели через api.brikko.ru проходят pre-LLM PII-маскинг
 * (reversible). Pill показываем на каждой карточке, чтобы посетитель видел:
 * privacy-инвариант не зависит от провайдера. Микро-pill (8px shield + 11px-text),
 * чтобы не конкурировать с tier-pill за внимание.
 */
function PrivacyV2Pill() {
  return (
    <span
      title="Все запросы проходят через pre-LLM PII-маскинг (reversible). Подробнее на /studio."
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        border: '1px solid var(--hairline)',
        borderRadius: 9999,
        background: 'var(--accent-1-soft)',
        fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
        fontSize: 9,
        fontWeight: 500,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: 'var(--fg-primary)',
      }}
    >
      <ShieldCheck className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      Privacy v2
    </span>
  );
}

function PriceCell({
  label,
  rubPer1m,
  muted,
}: {
  label: string;
  rubPer1m: number | null;
  muted?: boolean;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <dt
        style={{
          fontSize: 10,
          textTransform: 'uppercase',
          letterSpacing: '0.18em',
          color: 'var(--fg-faint)',
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
        }}
      >
        {label}
      </dt>
      <dd
        style={{
          marginTop: 2,
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 13,
          color: muted ? 'var(--fg-muted)' : 'var(--fg-primary)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {rubPer1m === null ? '—' : `${formatRub(rubPer1m)} / 1M`}
      </dd>
    </div>
  );
}

function CapabilityRow({
  capabilities,
}: {
  capabilities: PublicModel['capabilities'];
}) {
  const items = [
    {
      key: 'streaming',
      icon: <Activity className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
      title: 'Streaming (SSE)',
      on: capabilities.streaming,
    },
    {
      key: 'tool_calling',
      icon: <Wrench className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
      title: 'Tool calling',
      on: capabilities.tool_calling,
    },
    {
      key: 'json_schema_strict',
      icon: <Braces className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
      title: 'JSON Schema strict',
      on: capabilities.json_schema_strict,
    },
    {
      key: 'vision',
      icon: <Eye className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
      title: 'Vision (image input)',
      on: capabilities.vision,
    },
    {
      key: 'ru_legal',
      icon: <Shield className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />,
      title: 'Размещено в РФ (152-ФЗ)',
      on: capabilities.ru_legal,
    },
  ];

  return (
    <ul
      style={{ display: 'flex', flexWrap: 'wrap', gap: 6, listStyle: 'none', padding: 0, margin: 0 }}
      aria-label="Возможности модели"
    >
      {items.map((item) => (
        <li key={item.key}>
          <span
            title={item.on ? item.title : `${item.title} — не поддерживается`}
            aria-label={`${item.title}: ${item.on ? 'да' : 'нет'}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 8,
              border: '1px solid var(--hairline)',
              background: item.on ? 'var(--bg-base)' : 'transparent',
              color: item.on ? 'var(--fg-primary)' : 'var(--fg-faint)',
              opacity: item.on ? 1 : 0.4,
            }}
          >
            {item.icon}
          </span>
        </li>
      ))}
    </ul>
  );
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) {
    const m = tokens / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  if (tokens >= 1000) {
    return `${Math.round(tokens / 1000)}K`;
  }
  return formatTokens(tokens);
}

function formatDateRu(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date);
}
