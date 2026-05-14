'use client';

import { useEffect, useMemo, useState } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Banner } from '@/components/ui/banner';
import { RoutingModeToggle } from '@/components/dashboard/settings/RoutingModeToggle';
import { StrategyPresetPicker } from '@/components/dashboard/settings/StrategyPresetPicker';
import { CustomFilterEditor } from '@/components/dashboard/settings/CustomFilterEditor';
import { RoutingPreview } from '@/components/dashboard/settings/RoutingPreview';
import {
  useRoutingPreferences,
  useUpdateRoutingPreferences,
} from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';
import type {
  RoutingMode,
  RoutingPreferencesUpdate,
  RoutingProvider,
  RoutingStrategy,
} from '@/lib/types';

/**
 * Settings → Routing.
 *
 * UX-обоснование общей композиции:
 *  - **2-колоночный grid на desktop**: слева — форма (toggle, strategy, custom),
 *    справа sticky-preview. Юзер сразу видит как меняется набор моделей и цена.
 *  - **На mobile стек**: preview под формой, иначе sticky сожрал бы полэкрана.
 *  - **Save / Reset** в нижнем sticky-baré на форме (sm:absolute, sm:bottom-0).
 *    Это паттерн «edit then commit» — мы не auto-save'им (изменения routing-policy
 *    влияют на каждый платный запрос → юзер должен явно подтвердить).
 *
 * TODO Sprint 7.5: onboarding-tour (State D из спеки).
 * TODO Sprint 8: tariff-lock на ru_legal (AC-RP-11) — поле routing_locked.
 */

export default function RoutingPage() {
  const prefs = useRoutingPreferences();
  const update = useUpdateRoutingPreferences();

  if (prefs.isLoading) {
    return (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
        <Skeleton className="h-96" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (prefs.isError || !prefs.data) {
    return (
      <Banner
        variant="error"
        title="Не удалось загрузить настройки"
        description="Попробуй обновить страницу. Если ошибка повторится — напиши в поддержку."
      />
    );
  }

  return <RoutingForm initial={prefs.data} mutation={update} />;
}

interface RoutingFormProps {
  initial: NonNullable<ReturnType<typeof useRoutingPreferences>['data']>;
  mutation: ReturnType<typeof useUpdateRoutingPreferences>;
}

function RoutingForm({ initial, mutation }: RoutingFormProps) {
  const [mode, setMode] = useState<RoutingMode>(initial.routing_mode);
  const [strategy, setStrategy] = useState<RoutingStrategy>(initial.routing_strategy);
  const [providers, setProviders] = useState<RoutingProvider[]>(
    initial.allowed_providers ?? initial.available_providers.map((p) => p.id),
  );

  // Сбрасываем локальный state когда данные с сервера обновляются (например, после mutation).
  useEffect(() => {
    setMode(initial.routing_mode);
    setStrategy(initial.routing_strategy);
    setProviders(
      initial.allowed_providers ?? initial.available_providers.map((p) => p.id),
    );
  }, [initial]);

  const isCustom = strategy === 'custom';

  // Building payload в зависимости от стратегии.
  const payload: RoutingPreferencesUpdate = useMemo(
    () => ({
      routing_mode: mode,
      routing_strategy: strategy,
      allowed_providers: isCustom ? providers : null,
      // TODO Sprint 7.5: per-model whitelist в UI.
      allowed_models: null,
    }),
    [mode, strategy, isCustom, providers],
  );

  // Save disabled если: mode=manual ИЛИ нет изменений ИЛИ custom но 0 провайдеров.
  const hasChanges = useMemo(() => {
    if (mode !== initial.routing_mode) return true;
    if (strategy !== initial.routing_strategy) return true;
    if (isCustom) {
      const initialProviders = initial.allowed_providers ?? [];
      if (initialProviders.length !== providers.length) return true;
      const sortedA = [...initialProviders].sort();
      const sortedB = [...providers].sort();
      return sortedA.some((p, i) => p !== sortedB[i]);
    }
    return false;
  }, [mode, strategy, isCustom, providers, initial]);

  const canSave = hasChanges && !(isCustom && providers.length === 0);

  async function handleSave() {
    try {
      await mutation.mutateAsync(payload);
      toast.success('Настройки роутинга сохранены.');
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось сохранить.';
      toast.error(message);
    }
  }

  function handleReset() {
    setMode('smart');
    setStrategy('smart');
    setProviders(initial.available_providers.map((p) => p.id));
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
      {/* Левая колонка — форма */}
      <div className="flex flex-col gap-4">
        <RoutingModeToggle value={mode} onChange={setMode} disabled={mutation.isPending} />

        {mode === 'smart' ? (
          <>
            <StrategyPresetPicker
              value={strategy}
              onChange={setStrategy}
              disabled={mutation.isPending}
            />

            {isCustom ? (
              <CustomFilterEditor
                availableProviders={initial.available_providers}
                selectedProviders={providers}
                onChange={setProviders}
                disabled={mutation.isPending}
              />
            ) : null}
          </>
        ) : null}

        <div
          className="flex items-center gap-3 border-t border-gray-200 pt-4"
          data-testid="routing-form-actions"
        >
          <Button
            onClick={handleSave}
            disabled={!canSave}
            loading={mutation.isPending}
            data-testid="routing-save-button"
          >
            Сохранить
          </Button>
          <Button variant="ghost" onClick={handleReset} data-testid="routing-reset-button">
            Сбросить к Smart
          </Button>
        </div>
      </div>

      {/* Правая колонка — preview */}
      <RoutingPreview preview={initial.preview} pending={mutation.isPending} />
    </div>
  );
}
