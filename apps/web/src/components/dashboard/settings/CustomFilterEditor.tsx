'use client';

import { AlertTriangle } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import type { RoutingProvider, RoutingProviderInfo } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Editor для custom-стратегии: чекбоксы провайдеров.
 *
 * UX-обоснование:
 *  - **6 чекбоксов в 2 колонки** (на desktop), 1 колонка на mobile — компактно
 *    и провайдеры все видны без скролла. Не делаем «select все/none»
 *    кнопок: при 6 опциях это излишне, юзер кликнет 6 раз сам.
 *  - **Per-model whitelist (`allowed_models`)** — спрятан за «Show», т.к. 95%
 *    кейсов решаются на уровне провайдеров. Раскрытие — opt-in для advanced.
 *    TODO Sprint 7.5: per-model picker с группировкой по провайдеру.
 *  - **Failover-warning при 1 провайдере** — appears реактивно, не блокирует
 *    Save (это soft-warning), но зрительно меняет цвет блока.
 *
 * Ограничение Sprint 7: только провайдер-уровень. Per-model — TODO Sprint 7.5
 * (пользователю показываем UI-stub, чтобы сразу видеть, что фича запланирована).
 */

interface CustomFilterEditorProps {
  availableProviders: RoutingProviderInfo[];
  selectedProviders: RoutingProvider[];
  onChange: (next: RoutingProvider[]) => void;
  disabled?: boolean;
}

export function CustomFilterEditor({
  availableProviders,
  selectedProviders,
  onChange,
  disabled,
}: CustomFilterEditorProps) {
  const isOnlyOne = selectedProviders.length === 1;
  const isEmpty = selectedProviders.length === 0;

  function toggle(id: RoutingProvider, on: boolean) {
    const set = new Set(selectedProviders);
    if (on) set.add(id);
    else set.delete(id);
    onChange(Array.from(set));
  }

  return (
    <Card>
      <CardTitle>Разрешённые провайдеры</CardTitle>
      <CardDescription className="mt-1">
        Минимум 1 провайдер. Запросы пойдут только в выбранные.
      </CardDescription>

      <fieldset
        className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2"
        disabled={disabled}
        data-testid="custom-filter-providers"
      >
        <legend className="sr-only">Провайдеры</legend>
        {availableProviders.map((p) => {
          const checked = selectedProviders.includes(p.id);
          return (
            <label
              key={p.id}
              className={cn(
                'flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors',
                checked ? 'border-brand-600 bg-brand-50/40' : 'border-gray-200 hover:bg-gray-50',
                disabled && 'cursor-not-allowed opacity-60',
              )}
              data-testid={`custom-filter-provider-${p.id}`}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={(e) => toggle(p.id, e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
              />
              <span className="flex flex-1 flex-col gap-0.5">
                <span className="text-body font-medium text-gray-900">{p.label}</span>
                <span className="text-body-sm text-gray-500">{p.model_count} моделей</span>
              </span>
            </label>
          );
        })}
      </fieldset>

      {isEmpty ? (
        <p
          className="mt-4 flex items-start gap-2 rounded-md border border-error-200 bg-error-50 p-3 text-body-sm text-error-600"
          role="alert"
          data-testid="custom-filter-empty-error"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Выбери хотя бы одного провайдера, иначе сохранить нельзя.</span>
        </p>
      ) : null}

      {isOnlyOne ? (
        <p
          className="mt-4 flex items-start gap-2 rounded-md border border-warning-200 bg-warning-50 p-3 text-body-sm text-gray-900"
          data-testid="custom-filter-single-provider-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-600" aria-hidden="true" />
          <span>
            Только 1 провайдер — failover работать не будет. Если он упадёт, ты получишь 503.
            Чтобы сохранить отказоустойчивость — оставь хотя бы 2.
          </span>
        </p>
      ) : null}

      {/* TODO Sprint 7.5: per-model whitelist. */}
      <p className="mt-6 text-xs text-gray-500">
        Per-model whitelist (выбрать конкретные модели внутри провайдера) —{' '}
        <span className="italic">скоро в Sprint 7.5</span>.
      </p>
    </Card>
  );
}
