/**
 * <HelperTooltip>: lightweight (?) popover, без radix-tooltip.
 *
 * Что покрываем:
 *   - По умолчанию контент не виден (DOM-нода отсутствует).
 *   - Focus на кнопку — контент рендерится с role="tooltip" и aria-describedby связкой.
 *   - Blur — контент исчезает.
 *   - Click на кнопку — toggle open/close (для touch-устройств).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HelperTooltip } from '@/components/ui/helper-tooltip';

describe('<HelperTooltip>', () => {
  it('по умолчанию контент скрыт', () => {
    render(<HelperTooltip content="Привет, я подсказка" />);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('focus → контент появляется с role=tooltip и aria-describedby', async () => {
    const { act } = await import('@testing-library/react');
    render(<HelperTooltip content="Помогает отличать ключи" />);
    const button = screen.getByRole('button', { name: 'Подсказка' });
    act(() => {
      button.focus();
    });
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent('Помогает отличать ключи');
    expect(button).toHaveAttribute('aria-describedby', tooltip.id);
  });

  it('blur → контент скрывается', async () => {
    const { act } = await import('@testing-library/react');
    render(<HelperTooltip content="hello" />);
    const button = screen.getByRole('button');
    act(() => {
      button.focus();
    });
    await screen.findByRole('tooltip');
    act(() => {
      button.blur();
    });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('click — открывает (для touch)', async () => {
    const user = userEvent.setup();
    render(<HelperTooltip content="hello" />);
    const button = screen.getByRole('button');
    // На touch браузер вызывает focus + click. Tooltip должен быть виден.
    await user.click(button);
    expect(await screen.findByRole('tooltip')).toBeInTheDocument();
  });

  it('кастомный label попадает в aria-label кнопки', () => {
    render(<HelperTooltip content="x" label="Что такое welcome" />);
    expect(screen.getByRole('button', { name: 'Что такое welcome' })).toBeInTheDocument();
  });
});
