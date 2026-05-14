'use client';

import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import type { Route } from 'next';
import { useTransition, useCallback } from 'react';
import type { Provider, Tier } from '@/lib/models-fallback';

/**
 * Тонкий client-component: рендерит чекбоксы и пушит state в URL
 * через `router.replace` (без full reload). Сам каталог остаётся
 * server-rendered — мы платим только за гидрацию sidebar'а.
 *
 * UX-обоснование: filter в URL = shareable + browser back/forward работают,
 * + страница доступна без JS (поисковики увидят полный каталог по дефолту).
 *
 * `useTransition` помечает обновление как non-blocking, чтобы клик по
 * чекбоксу мгновенно отрисовывал новое состояние, а server-component
 * перерисовывался в фоне без UI-фриза.
 */

interface FilterOption<T extends string> {
  value: T;
  label: string;
  /** Сколько моделей подходит под этот фильтр — показываем рядом с лейблом. */
  count: number;
}

interface ModelsFiltersProps {
  providers: FilterOption<Provider>[];
  tiers: FilterOption<Tier>[];
  capabilities: FilterOption<CapabilityKey>[];
  totalCount: number;
  filteredCount: number;
}

export type CapabilityKey =
  | 'streaming'
  | 'tool_calling'
  | 'json_schema_strict'
  | 'vision'
  | 'ru_legal'
  | 'prompt_caching';

/** Параметры — мульти-значные через `?provider=openai&provider=anthropic` */
const PARAM_PROVIDER = 'provider';
const PARAM_TIER = 'tier';
const PARAM_CAPABILITY = 'capability';

export function ModelsFilters({
  providers,
  tiers,
  capabilities,
  totalCount,
  filteredCount,
}: ModelsFiltersProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const toggleParam = useCallback(
    (paramName: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      const current = params.getAll(paramName);
      params.delete(paramName);

      if (current.includes(value)) {
        // снять чекбокс — удалить ровно это значение
        for (const v of current) if (v !== value) params.append(paramName, v);
      } else {
        // добавить — все старые + новое
        for (const v of current) params.append(paramName, v);
        params.append(paramName, value);
      }

      const qs = params.toString();
      const url = qs ? `${pathname}?${qs}` : pathname;
      // `scroll: false` — не дёргаем юзера в начало страницы при смене фильтров.
      // `replace`, не `push` — чтобы серия кликов не засоряла history.
      // typedRoutes (next.config.mjs) валидирует pathname'ы из source-кода;
      // `?provider=…` динамика — поэтому cast на Route после ручной сборки.
      startTransition(() => {
        router.replace(url as Route, { scroll: false });
      });
    },
    [pathname, router, searchParams],
  );

  const reset = useCallback(() => {
    startTransition(() => {
      router.replace(pathname as Route, { scroll: false });
    });
  }, [pathname, router]);

  const activeProviders = new Set(searchParams.getAll(PARAM_PROVIDER));
  const activeTiers = new Set(searchParams.getAll(PARAM_TIER));
  const activeCapabilities = new Set(searchParams.getAll(PARAM_CAPABILITY));

  const hasActiveFilters =
    activeProviders.size > 0 || activeTiers.size > 0 || activeCapabilities.size > 0;

  return (
    <aside
      aria-label="Фильтры каталога моделей"
      className="lg:sticky lg:top-32"
      data-pending={isPending ? 'true' : undefined}
      style={{ color: 'var(--fg-primary)' }}
    >
      <div className="flex items-center justify-between">
        <h2
          style={{
            fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
            fontWeight: 400,
            fontSize: 22,
            letterSpacing: '-0.01em',
            color: 'var(--fg-primary)',
          }}
        >
          Фильтры
        </h2>
        {hasActiveFilters ? (
          <button
            type="button"
            onClick={reset}
            style={{
              background: 'transparent',
              border: 0,
              padding: 0,
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--fg-muted)',
              textDecoration: 'underline',
              cursor: 'pointer',
            }}
          >
            Сбросить
          </button>
        ) : null}
      </div>

      <p
        style={{ marginTop: 6, fontSize: 13, color: 'var(--fg-faint)' }}
        aria-live="polite"
      >
        Показано {filteredCount} из {totalCount}
      </p>

      <FilterGroup title="Провайдер">
        {providers.map((opt) => (
          <FilterCheckbox
            key={opt.value}
            label={opt.label}
            count={opt.count}
            checked={activeProviders.has(opt.value)}
            onToggle={() => toggleParam(PARAM_PROVIDER, opt.value)}
          />
        ))}
      </FilterGroup>

      <FilterGroup title="Возможности">
        {capabilities.map((opt) => (
          <FilterCheckbox
            key={opt.value}
            label={opt.label}
            count={opt.count}
            checked={activeCapabilities.has(opt.value)}
            onToggle={() => toggleParam(PARAM_CAPABILITY, opt.value)}
          />
        ))}
      </FilterGroup>

      <FilterGroup title="Уровень">
        {tiers.map((opt) => (
          <FilterCheckbox
            key={opt.value}
            label={opt.label}
            count={opt.count}
            checked={activeTiers.has(opt.value)}
            onToggle={() => toggleParam(PARAM_TIER, opt.value)}
          />
        ))}
      </FilterGroup>
    </aside>
  );
}

function FilterGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset style={{ marginTop: 24, padding: 0, border: 0 }}>
      <legend
        style={{
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 10,
          fontWeight: 500,
          letterSpacing: '0.2em',
          textTransform: 'uppercase',
          color: 'var(--fg-faint)',
          padding: 0,
        }}
      >
        {title}
      </legend>
      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {children}
      </div>
    </fieldset>
  );
}

function FilterCheckbox({
  label,
  count,
  checked,
  onToggle,
}: {
  label: string;
  count: number;
  checked: boolean;
  onToggle: () => void;
}) {
  const disabled = count === 0 && !checked;
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '6px 8px',
        borderRadius: 8,
        fontSize: 13,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        color: checked ? 'var(--fg-primary)' : 'var(--fg-muted)',
        fontWeight: checked ? 500 : 400,
        transition: 'background 200ms var(--ease-out-quint), color 240ms var(--ease-out-quint)',
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = 'var(--bg-elevated)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent';
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={onToggle}
          style={{
            width: 16,
            height: 16,
            margin: 0,
            accentColor: 'var(--accent-1)',
          }}
        />
        <span>{label}</span>
      </span>
      <span
        style={{
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 11,
          color: 'var(--fg-faint)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {count}
      </span>
    </label>
  );
}
