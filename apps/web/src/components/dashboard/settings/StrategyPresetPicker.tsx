'use client';

import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import type { RoutingStrategy } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Radio-picker для 5 стратегий: cheap / smart / fast / ru_legal / custom.
 *
 * UX-обоснование:
 *  - **Radio (а не select)** — пять опций видны сразу с описаниями. Select прятал
 *    бы знание «что есть RU-only» за клик; для одной из ключевых compliance-фич
 *    это плохо.
 *  - **Inline-warnings** (под cheap, под ru_legal) — формирую ожидания ДО клика
 *    Save, а не валидацией после. Снижает support-загрузку.
 *  - **Disabled-state с подсказкой** — для tariff-locked сценариев (ru_legal forced).
 *    TODO Sprint 8: implement tariff-lock когда BE добавит флаг routing_locked.
 */

interface StrategyPresetPickerProps {
  value: RoutingStrategy;
  onChange: (next: RoutingStrategy) => void;
  disabled?: boolean;
  /** TODO Sprint 8: список заблокированных стратегий (например, tariff-lock на ru_legal). */
  lockedStrategies?: RoutingStrategy[];
}

interface PresetOption {
  id: RoutingStrategy;
  label: string;
  description: string;
  /** Soft-warning под radio. */
  caveat?: string;
}

const OPTIONS: PresetOption[] = [
  {
    id: 'cheap',
    label: 'Cheap',
    description: 'Минимальная цена. Подходит для бытового чата и коротких саммари.',
    caveat: 'Может выбрать слабую модель для кода/рассуждения. Для сложного — Smart.',
  },
  {
    id: 'smart',
    label: 'Smart',
    description: 'Баланс качества и цены. Выбор по умолчанию для большинства кейсов.',
  },
  {
    id: 'fast',
    label: 'Fast',
    description: 'Минимальная p50-латентность. Для чат-UI и tool-use циклов.',
  },
  {
    id: 'ru_legal',
    label: 'RU-only',
    description: 'Только российские провайдеры (YandexGPT, GigaChat). 152-ФЗ-чувствительные кейсы.',
    caveat: 'Исключает OpenAI / Anthropic / Google. Проверь, что Yandex/GigaChat хватает для твоего кейса.',
  },
  {
    id: 'custom',
    label: 'Custom',
    description: 'Самостоятельно выбираешь провайдеров и модели.',
  },
];

export function StrategyPresetPicker({
  value,
  onChange,
  disabled,
  lockedStrategies = [],
}: StrategyPresetPickerProps) {
  return (
    <Card>
      <CardTitle>Стратегия</CardTitle>
      <CardDescription className="mt-1">
        По чему gateway выбирает модель внутри Smart Routing.
      </CardDescription>

      <fieldset
        className="mt-4 flex flex-col gap-2"
        disabled={disabled}
        data-testid="strategy-preset-picker"
      >
        <legend className="sr-only">Стратегия Smart Routing</legend>
        {OPTIONS.map((opt) => {
          const locked = lockedStrategies.includes(opt.id);
          const checked = value === opt.id;
          return (
            <label
              key={opt.id}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors',
                'focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand-600',
                checked ? 'border-brand-600 bg-brand-50/40' : 'border-gray-200 hover:bg-gray-50',
                (disabled || locked) && 'cursor-not-allowed opacity-60',
              )}
              data-testid={`strategy-preset-${opt.id}`}
            >
              <input
                type="radio"
                name="routing-strategy"
                value={opt.id}
                checked={checked}
                disabled={disabled || locked}
                onChange={() => onChange(opt.id)}
                className="mt-1 h-4 w-4 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
              />
              <span className="flex flex-1 flex-col gap-0.5">
                <span className="text-body font-medium text-gray-900">{opt.label}</span>
                <span className="text-body-sm text-gray-600">{opt.description}</span>
                {opt.caveat && checked ? (
                  <span className="mt-1 text-xs text-gray-500" data-testid={`strategy-caveat-${opt.id}`}>
                    {opt.caveat}
                  </span>
                ) : null}
                {locked ? (
                  <span className="mt-1 text-xs text-warning-600">
                    Заблокировано тарифом — изменить нельзя.
                  </span>
                ) : null}
              </span>
            </label>
          );
        })}
      </fieldset>
    </Card>
  );
}
