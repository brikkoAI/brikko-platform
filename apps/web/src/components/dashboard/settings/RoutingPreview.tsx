'use client';

import { Sparkles, AlertTriangle, Info } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import type { RoutingPreview as RoutingPreviewData } from '@/lib/types';
import { formatRub } from '@/lib/utils';

/**
 * Preview-блок: какие модели сейчас активны при выбранном filter'е +
 * estimated avg cost.
 *
 * UX-обоснование:
 *  - **Sticky на desktop, под формой на mobile** — на широких экранах юзер
 *    хочет видеть «что я меняю» одновременно с radio'ами; на узких скролл
 *    оправданнее, чем sticky-overlay.
 *  - **Estimated cost — backend-driven** (см. §3.4 спеки): frontend только
 *    рендерит число. Юзеру важно понимать, что это ОЦЕНКА (надпись «~»),
 *    а не точный прогноз.
 *  - **Top-5 моделей** + общий счётчик: компактно, но даёт понимание масштаба.
 */

interface RoutingPreviewProps {
  preview: RoutingPreviewData;
  /** Текст pending-state'а пока mutation в полёте. */
  pending?: boolean;
}

const WARNING_LABELS: Record<string, string> = {
  failover_disabled_single_provider:
    'Failover отключён — только 1 провайдер в фильтре.',
  no_models_for_code_category:
    'Под выбранный фильтр нет моделей для категории code — code-запросы провалятся.',
  no_eligible_provider_after_exclusion:
    'Per-request exclude_providers пересекается с whitelist до пустого набора.',
};

export function RoutingPreview({ preview, pending }: RoutingPreviewProps) {
  const costRub = preview.estimated_avg_cost_kop_per_1m_tokens / 100;
  return (
    <Card
      // На desktop — sticky-right (parent layout управляет grid'ом). На mobile —
      // обычный поток. Sticky только sm+, потому что на mobile sticky закрыл бы
      // полформы и UX был бы хуже.
      className="lg:sticky lg:top-24"
      data-testid="routing-preview"
    >
      <CardTitle className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
        Предпросмотр
      </CardTitle>
      <CardDescription className="mt-1">
        Модели, которые активны при текущем фильтре.
      </CardDescription>

      <div className="mt-4 space-y-4">
        <div>
          <p className="text-body-sm font-medium text-gray-700">
            Активные модели ({preview.active_models_total})
          </p>
          {preview.active_models.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-1" data-testid="routing-preview-models">
              {preview.active_models.map((model) => (
                <li
                  key={model}
                  className="rounded-md border border-gray-200 bg-gray-50 px-2 py-1 font-mono text-xs text-gray-700"
                >
                  {model}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-body-sm text-gray-500">
              Под текущий фильтр моделей нет.
            </p>
          )}
        </div>

        <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
          <p className="text-xs text-gray-500">Средняя цена за 1M токенов</p>
          <p
            className="mt-1 text-lg font-semibold text-gray-900"
            data-testid="routing-preview-cost"
          >
            ~{formatRub(costRub, costRub < 100 ? 2 : 0)}
          </p>
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-gray-500">
            <Info className="h-3 w-3" aria-hidden="true" />
            Ориентир. Реальная цена зависит от категории запросов.
          </p>
        </div>

        {preview.warnings.length > 0 ? (
          <ul className="space-y-2" data-testid="routing-preview-warnings">
            {preview.warnings.map((w) => (
              <li
                key={w}
                className="flex items-start gap-2 rounded-md border border-warning-200 bg-warning-50 p-2 text-xs text-gray-900"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning-600" aria-hidden="true" />
                <span>{WARNING_LABELS[w] ?? w}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {pending ? (
          <p className="text-xs text-gray-500">Сохраняем изменения…</p>
        ) : null}
      </div>
    </Card>
  );
}
