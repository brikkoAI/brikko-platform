/**
 * UsageRoutingBadges (Sprint 8 §6).
 *
 * Что покрываем:
 *   - Если backend ничего не прислал — компонент рендерит null (нет лишнего layout shift).
 *   - strategy='cheap' — рендерит chip с лейблом cheap + tooltip-кнопка.
 *   - routing_model_chosen != model — рендерит badge «→ <model>» (роутер заменил модель).
 *   - routing_model_chosen == model — badge не рендерится (избыточно).
 *   - routing_fallback_used=true — рендерит «failover» с warning иконкой.
 *   - Все три поля одновременно — все три badges на месте.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { toKopecks, type UsageItem } from '@/lib/types';
import { UsageRoutingBadges } from '@/components/dashboard/UsageRoutingBadges';

function makeItem(over: Partial<UsageItem>): UsageItem {
  return {
    date: null,
    model: 'gpt-5.4-mini',
    key_id: null,
    tokens_in: 0,
    tokens_out: 0,
    cached_tokens: 0,
    cost_kop: toKopecks(0),
    ...over,
  };
}

describe('<UsageRoutingBadges> (Sprint 8)', () => {
  it('возвращает null когда backend ничего не прислал', () => {
    const { container } = render(<UsageRoutingBadges item={makeItem({})} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('рендерит chip strategy=cheap', () => {
    render(<UsageRoutingBadges item={makeItem({ routing_strategy: 'cheap' })} />);
    expect(screen.getByTestId('routing-strategy-cheap')).toBeInTheDocument();
    expect(screen.getByTestId('routing-strategy-cheap').textContent).toBe('cheap');
  });

  it('рендерит chip strategy=ru_legal', () => {
    render(<UsageRoutingBadges item={makeItem({ routing_strategy: 'ru_legal' })} />);
    expect(screen.getByTestId('routing-strategy-ru_legal')).toBeInTheDocument();
  });

  it('routing_model_chosen != model — рендерит chip с альтернативной моделью', () => {
    render(
      <UsageRoutingBadges
        item={makeItem({
          model: 'gpt-5.4',
          routing_model_chosen: 'gpt-5.4-mini',
        })}
      />,
    );
    const chip = screen.getByTestId('routing-model-chosen');
    expect(chip).toBeInTheDocument();
    expect(chip.textContent).toContain('gpt-5.4-mini');
  });

  it('routing_model_chosen == model — chip не рендерится', () => {
    render(
      <UsageRoutingBadges
        item={makeItem({
          model: 'gpt-5.4-mini',
          routing_model_chosen: 'gpt-5.4-mini',
        })}
      />,
    );
    expect(screen.queryByTestId('routing-model-chosen')).not.toBeInTheDocument();
  });

  it('failover_used=true — рендерит warning chip', () => {
    render(
      <UsageRoutingBadges
        item={makeItem({ routing_fallback_used: true })}
      />,
    );
    const fb = screen.getByTestId('routing-fallback');
    expect(fb).toBeInTheDocument();
    expect(fb.textContent).toMatch(/failover/);
  });

  it('failover_used=false — chip не рендерится', () => {
    render(
      <UsageRoutingBadges
        item={makeItem({ routing_strategy: 'smart', routing_fallback_used: false })}
      />,
    );
    expect(screen.queryByTestId('routing-fallback')).not.toBeInTheDocument();
  });

  it('все три поля одновременно — все три chip на месте', () => {
    render(
      <UsageRoutingBadges
        item={makeItem({
          model: 'gpt-5.4',
          routing_strategy: 'smart',
          routing_model_chosen: 'claude-sonnet-4.6',
          routing_fallback_used: true,
        })}
      />,
    );
    expect(screen.getByTestId('routing-strategy-smart')).toBeInTheDocument();
    expect(screen.getByTestId('routing-model-chosen')).toBeInTheDocument();
    expect(screen.getByTestId('routing-fallback')).toBeInTheDocument();
  });
});
