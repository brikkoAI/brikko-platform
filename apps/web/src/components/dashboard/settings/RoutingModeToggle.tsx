'use client';

import { Route } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import type { RoutingMode } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Manual ↔ Smart routing toggle.
 *
 * UX-обоснование (см. CEO 30.04 §10):
 *  - **Toggle (а не tabs)**: это binary-switch с явным «default-on» состоянием Smart.
 *    Tabs создавали бы ощущение что обе стороны равноправны — это не так,
 *    98% юзеров остаются в Smart. Toggle — короче зрительно и сразу понятно
 *    «выкл/вкл умного роутера».
 *  - **Inline-описания** под обеими опциями нужны на первом контакте; для повторных
 *    визитов можно было бы свернуть, но в Settings UX-overhead малый, оставляем.
 */

interface RoutingModeToggleProps {
  value: RoutingMode;
  onChange: (next: RoutingMode) => void;
  disabled?: boolean;
}

export function RoutingModeToggle({ value, onChange, disabled }: RoutingModeToggleProps) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col">
          <CardTitle className="flex items-center gap-2">
            <Route className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
            Smart Routing
          </CardTitle>
          <CardDescription className="mt-1">
            {value === 'smart'
              ? 'Brikko сам выбирает модель под запрос — по стратегии ниже.'
              : 'Ты передаёшь модель в каждом запросе через `model: "gpt-5.4-mini"`.'}
          </CardDescription>
        </div>

        <button
          type="button"
          role="switch"
          aria-checked={value === 'smart'}
          disabled={disabled}
          onClick={() => onChange(value === 'smart' ? 'manual' : 'smart')}
          className={cn(
            'relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600',
            'disabled:cursor-not-allowed disabled:opacity-50',
            value === 'smart' ? 'bg-brand-600' : 'bg-gray-300',
          )}
          data-testid="routing-mode-toggle"
        >
          <span
            className={cn(
              'inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform',
              value === 'smart' ? 'translate-x-6' : 'translate-x-1',
            )}
          />
          <span className="sr-only">
            {value === 'smart' ? 'Smart Routing включён' : 'Smart Routing выключен'}
          </span>
        </button>
      </div>

      {value === 'manual' ? (
        <p className="mt-4 rounded-md border border-gray-200 bg-gray-50 p-3 text-body-sm text-gray-600">
          Отправлять `auto:*` нельзя — gateway вернёт 400. Используй явные ID моделей.{' '}
          <a href="/docs#models" className="text-brand-600 hover:underline">
            Каталог моделей →
          </a>
        </p>
      ) : null}
    </Card>
  );
}
