'use client';

import { AlertTriangle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { HelperTooltip } from '@/components/ui/helper-tooltip';
import type { RoutingDecisionStrategy, UsageItem } from '@/lib/types';

/**
 * UsageRoutingBadges — отображает X-Router-Decision метаданные (Sprint 8 §6).
 *
 * Контракт:
 *   - `routing_strategy` — null/undefined значит manual или explicit-model запрос (badge не рендерится).
 *   - `routing_model_chosen` — фактически использованная модель (нужна когда роутер
 *     заменил запрошенную: пользователь шлёт auto:cheap, роутер выбирает gpt-5.4-mini).
 *   - `routing_fallback_used` — true если основной провайдер упал, роутер ушёл на резервного.
 *
 * UX-обоснование:
 *   - Маленькие неинтрузивные badges рядом с моделью, чтобы не сломать table-layout.
 *   - Tooltip над strategy chip — объясняет что значит «cheap», без ссылки на /docs
 *     (ещё один клик — лишний). Цвет нейтральный (gray), чтобы не конкурировать с
 *     основной информацией строки (стоимость).
 *   - Fallback-icon (warning) — единственный «attention» сигнал; пользователь видит
 *     что роутер сработал и может проверить, не настроить ли ему ru_legal стратегию.
 */

const STRATEGY_LABELS: Record<RoutingDecisionStrategy, string> = {
  cheap: 'cheap',
  smart: 'smart',
  fast: 'fast',
  ru_legal: 'ru_legal',
  custom: 'custom',
};

const STRATEGY_TOOLTIPS: Record<RoutingDecisionStrategy, string> = {
  cheap: 'Минимальная цена за 1M токенов. Дефолтная стратегия — экономит 60-80% на одинаковых задачах.',
  smart: 'Баланс цены и качества. Топ-модель для complex-промптов, дешёвая для простых.',
  fast: 'Минимальная p50-латентность. Используется для real-time UX (чат, voice).',
  ru_legal: 'Только RU-hosted провайдеры (YandexGPT, GigaChat) — для 152-ФЗ и ПДн.',
  custom: 'Кастомный whitelist провайдеров и/или моделей.',
};

interface Props {
  item: Pick<
    UsageItem,
    'routing_strategy' | 'routing_model_chosen' | 'routing_fallback_used' | 'model'
  >;
}

export function UsageRoutingBadges({ item }: Props) {
  const strategy = item.routing_strategy ?? null;
  const modelChosen = item.routing_model_chosen ?? null;
  const fallback = item.routing_fallback_used ?? false;

  // Если backend ничего не прислал — пустой fragment, table-cell остаётся без шума.
  const hasAny = strategy !== null || (modelChosen && modelChosen !== item.model) || fallback;
  if (!hasAny) return null;

  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="usage-routing-badges">
      {strategy ? (
        <span className="inline-flex items-center gap-1">
          <Badge variant="brand" data-testid={`routing-strategy-${strategy}`}>
            {STRATEGY_LABELS[strategy]}
          </Badge>
          <HelperTooltip
            label={`Что значит стратегия ${strategy}`}
            content={STRATEGY_TOOLTIPS[strategy]}
          />
        </span>
      ) : null}

      {modelChosen && modelChosen !== item.model ? (
        <Badge
          variant="neutral"
          aria-label={`Фактически использована модель ${modelChosen}`}
          data-testid="routing-model-chosen"
        >
          → <code className="font-mono text-[11px]">{modelChosen}</code>
        </Badge>
      ) : null}

      {fallback ? (
        <span
          className="inline-flex items-center gap-1 text-warning-600"
          aria-label="Использован резервный провайдер (failover)"
          data-testid="routing-fallback"
          title="Основной провайдер вернул ошибку — запрос ушёл на резервного. См. Settings → Routing."
        >
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="text-xs font-medium">failover</span>
        </span>
      ) : null}
    </div>
  );
}
