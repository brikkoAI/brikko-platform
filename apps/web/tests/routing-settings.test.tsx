/**
 * Routing settings UI — Sprint 7.
 *
 * Что покрываем:
 *  - <RoutingModeToggle>: aria-checked=true когда mode=smart, switch toggle'ит.
 *  - <RoutingModeToggle>: manual mode → отдельный hint про auto:* запреты.
 *  - <StrategyPresetPicker>: рендерит 5 опций; смена выбора вызывает onChange.
 *  - <StrategyPresetPicker>: locked-стратегия → disabled + tooltip.
 *  - <CustomFilterEditor>: empty selection → error-banner.
 *  - <CustomFilterEditor>: 1 провайдер → warning «failover отключён».
 *  - <CustomFilterEditor>: ≥2 провайдеров → нет warning'ов.
 *  - <CustomFilterEditor>: toggle провайдера → onChange с новым массивом.
 *  - <RoutingPreview>: рендерит top-5 моделей.
 *  - <RoutingPreview>: warnings отображаются с локализованным текстом.
 *  - <RoutingPreview>: cost рендерится с символом «~».
 *  - Routing page: Save disabled когда нет изменений.
 *  - Routing page: смена strategy → Save enable'ится.
 *  - Routing page: Reset to Smart возвращает дефолты.
 *  - Routing page: error-state → банер «Не удалось загрузить».
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { RoutingPreferences } from '@/lib/types';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const useRoutingPreferencesReturn = vi.fn();
const updateMock = vi.fn();
vi.mock('@/lib/auth', () => ({
  useRoutingPreferences: () => useRoutingPreferencesReturn(),
  useUpdateRoutingPreferences: () => ({
    mutateAsync: updateMock,
    isPending: false,
  }),
}));

const { RoutingModeToggle } = await import(
  '@/components/dashboard/settings/RoutingModeToggle'
);
const { StrategyPresetPicker } = await import(
  '@/components/dashboard/settings/StrategyPresetPicker'
);
const { CustomFilterEditor } = await import(
  '@/components/dashboard/settings/CustomFilterEditor'
);
const { RoutingPreview } = await import(
  '@/components/dashboard/settings/RoutingPreview'
);
const { default: RoutingPage } = await import('@/app/app/settings/routing/page');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

function buildPrefs(overrides?: Partial<RoutingPreferences>): RoutingPreferences {
  return {
    routing_mode: 'smart',
    routing_strategy: 'cheap',
    allowed_providers: null,
    allowed_models: null,
    preview: {
      active_models: ['gpt-5.4-mini', 'claude-haiku-4.5', 'deepseek-v3.2-chat'],
      active_models_total: 12,
      estimated_avg_cost_kop_per_1m_tokens: 800,
      warnings: [],
    },
    available_providers: [
      { id: 'openai', label: 'OpenAI', model_count: 5 },
      { id: 'anthropic', label: 'Anthropic', model_count: 3 },
      { id: 'yandex', label: 'Yandex', model_count: 2 },
    ],
    updated_at: null,
    ...overrides,
  };
}

describe('<RoutingModeToggle>', () => {
  it('aria-checked=true для smart mode', () => {
    const onChange = vi.fn();
    render(<RoutingModeToggle value="smart" onChange={onChange} />);
    expect(screen.getByTestId('routing-mode-toggle')).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('clicks toggle между smart и manual', async () => {
    const onChange = vi.fn();
    render(<RoutingModeToggle value="smart" onChange={onChange} />);
    await userEvent.click(screen.getByTestId('routing-mode-toggle'));
    expect(onChange).toHaveBeenCalledWith('manual');
  });

  it('manual mode → hint о запрете auto:*', () => {
    render(<RoutingModeToggle value="manual" onChange={vi.fn()} />);
    expect(screen.getByText(/Отправлять `auto:\*` нельзя/)).toBeInTheDocument();
  });
});

describe('<StrategyPresetPicker>', () => {
  it('рендерит 5 опций', () => {
    render(<StrategyPresetPicker value="cheap" onChange={vi.fn()} />);
    expect(screen.getByTestId('strategy-preset-cheap')).toBeInTheDocument();
    expect(screen.getByTestId('strategy-preset-smart')).toBeInTheDocument();
    expect(screen.getByTestId('strategy-preset-fast')).toBeInTheDocument();
    expect(screen.getByTestId('strategy-preset-ru_legal')).toBeInTheDocument();
    expect(screen.getByTestId('strategy-preset-custom')).toBeInTheDocument();
  });

  it('смена выбора → onChange', async () => {
    const onChange = vi.fn();
    render(<StrategyPresetPicker value="cheap" onChange={onChange} />);
    await userEvent.click(screen.getByTestId('strategy-preset-ru_legal'));
    expect(onChange).toHaveBeenCalledWith('ru_legal');
  });

  it('locked стратегия → input disabled', () => {
    render(
      <StrategyPresetPicker
        value="cheap"
        onChange={vi.fn()}
        lockedStrategies={['smart']}
      />,
    );
    const lockedInput = screen
      .getByTestId('strategy-preset-smart')
      .querySelector('input') as HTMLInputElement;
    expect(lockedInput).toBeDisabled();
  });
});

describe('<CustomFilterEditor>', () => {
  const providers = [
    { id: 'openai' as const, label: 'OpenAI', model_count: 5 },
    { id: 'yandex' as const, label: 'Yandex', model_count: 2 },
    { id: 'sber' as const, label: 'GigaChat', model_count: 2 },
  ];

  it('empty selection → error-banner', () => {
    render(
      <CustomFilterEditor
        availableProviders={providers}
        selectedProviders={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('custom-filter-empty-error')).toBeInTheDocument();
  });

  it('1 провайдер → failover warning', () => {
    render(
      <CustomFilterEditor
        availableProviders={providers}
        selectedProviders={['openai']}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId('custom-filter-single-provider-warning'),
    ).toBeInTheDocument();
  });

  it('≥2 провайдеров → нет warning-блоков', () => {
    render(
      <CustomFilterEditor
        availableProviders={providers}
        selectedProviders={['openai', 'yandex']}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('custom-filter-empty-error')).toBeNull();
    expect(screen.queryByTestId('custom-filter-single-provider-warning')).toBeNull();
  });

  it('toggle провайдера вызывает onChange с обновлённым массивом', async () => {
    const onChange = vi.fn();
    render(
      <CustomFilterEditor
        availableProviders={providers}
        selectedProviders={['openai']}
        onChange={onChange}
      />,
    );
    const yandexLabel = screen.getByTestId('custom-filter-provider-yandex');
    await userEvent.click(yandexLabel.querySelector('input') as HTMLInputElement);
    expect(onChange).toHaveBeenCalledWith(['openai', 'yandex']);
  });
});

describe('<RoutingPreview>', () => {
  it('рендерит top-5 моделей и cost', () => {
    render(
      <RoutingPreview
        preview={{
          active_models: ['gpt-5.4-mini', 'claude-haiku-4.5'],
          active_models_total: 6,
          estimated_avg_cost_kop_per_1m_tokens: 800,
          warnings: [],
        }}
      />,
    );
    expect(screen.getByTestId('routing-preview-models')).toBeInTheDocument();
    expect(screen.getByTestId('routing-preview-cost').textContent).toMatch(/~/);
  });

  it('warnings рендерятся с локализованным текстом', () => {
    render(
      <RoutingPreview
        preview={{
          active_models: [],
          active_models_total: 0,
          estimated_avg_cost_kop_per_1m_tokens: 0,
          warnings: ['failover_disabled_single_provider'],
        }}
      />,
    );
    expect(screen.getByTestId('routing-preview-warnings')).toBeInTheDocument();
    expect(screen.getByText(/Failover отключён/)).toBeInTheDocument();
  });
});

describe('<RoutingPage>', () => {
  beforeEach(() => {
    useRoutingPreferencesReturn.mockReset();
    updateMock.mockReset();
  });

  it('error-state → banner «Не удалось загрузить»', () => {
    useRoutingPreferencesReturn.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(withQueryClient(<RoutingPage />));
    expect(screen.getByText(/Не удалось загрузить/)).toBeInTheDocument();
  });

  it('Save disabled когда нет изменений', () => {
    useRoutingPreferencesReturn.mockReturnValue({
      data: buildPrefs(),
      isLoading: false,
      isError: false,
    });
    render(withQueryClient(<RoutingPage />));
    expect(screen.getByTestId('routing-save-button')).toBeDisabled();
  });

  it('смена strategy → Save enabled, mutation вызывается', async () => {
    useRoutingPreferencesReturn.mockReturnValue({
      data: buildPrefs(),
      isLoading: false,
      isError: false,
    });
    updateMock.mockResolvedValue(buildPrefs({ routing_strategy: 'ru_legal' }));
    render(withQueryClient(<RoutingPage />));
    await userEvent.click(screen.getByTestId('strategy-preset-ru_legal'));
    expect(screen.getByTestId('routing-save-button')).not.toBeDisabled();
    await userEvent.click(screen.getByTestId('routing-save-button'));
    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith(
        expect.objectContaining({ routing_strategy: 'ru_legal' }),
      ),
    );
  });

  it('custom стратегия + 0 провайдеров → Save disabled', async () => {
    useRoutingPreferencesReturn.mockReturnValue({
      data: buildPrefs(),
      isLoading: false,
      isError: false,
    });
    render(withQueryClient(<RoutingPage />));
    await userEvent.click(screen.getByTestId('strategy-preset-custom'));
    // Снимаем все провайдеры через toggle.
    const inputs = screen
      .getByTestId('custom-filter-providers')
      .querySelectorAll('input[type="checkbox"]');
    for (const input of Array.from(inputs)) {
      await userEvent.click(input as HTMLInputElement);
    }
    expect(screen.getByTestId('routing-save-button')).toBeDisabled();
  });

  it('Reset to Smart возвращает дефолты', async () => {
    useRoutingPreferencesReturn.mockReturnValue({
      data: buildPrefs({ routing_strategy: 'ru_legal' }),
      isLoading: false,
      isError: false,
    });
    render(withQueryClient(<RoutingPage />));
    await userEvent.click(screen.getByTestId('routing-reset-button'));
    // Smart radio должен стать checked.
    const smartInput = screen
      .getByTestId('strategy-preset-smart')
      .querySelector('input') as HTMLInputElement;
    expect(smartInput.checked).toBe(true);
  });
});
