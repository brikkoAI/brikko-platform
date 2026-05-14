/**
 * Закрывает TD-005: контракт Button asChild + leftIcon/rightIcon/loading + Fragment.
 *
 * Регрессия, которую тесты ловят:
 *   1. `<Button asChild><Link>...</Link></Button>` — single child, рендерится Slot.
 *   2. `<Button asChild leftIcon><Link>...</Link></Button>` — иконка попадает ВНУТРЬ
 *      рендера child'а (иначе Radix Slot падает с React error #143).
 *   3. `<Button asChild loading>...` — спиннер заменяет leftIcon.
 *   4. `<Button asChild><><Link/></></Button>` — Fragment children: исторический
 *      падающий кейс. Теперь распаковывается и не бросает (TD-005).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Button } from '@/components/ui/button';

function Icon({ testid }: { testid: string }) {
  return <svg data-testid={testid} aria-hidden="true" />;
}

describe('<Button>', () => {
  it('рендерит обычный <button> без asChild', () => {
    render(<Button>Поехали</Button>);
    const btn = screen.getByRole('button', { name: 'Поехали' });
    expect(btn.tagName).toBe('BUTTON');
    // По умолчанию type=button, чтобы случайный submit не запускал форму.
    expect(btn).toHaveAttribute('type', 'button');
  });

  it('рендерит leftIcon и rightIcon в обычном режиме', () => {
    render(
      <Button leftIcon={<Icon testid="left" />} rightIcon={<Icon testid="right" />}>
        Hello
      </Button>,
    );
    expect(screen.getByTestId('left')).toBeInTheDocument();
    expect(screen.getByTestId('right')).toBeInTheDocument();
    expect(screen.getByRole('button')).toHaveTextContent('Hello');
  });

  it('loading=true: показывает спиннер, скрывает leftIcon, ставит aria-busy', () => {
    render(
      <Button loading leftIcon={<Icon testid="left" />}>
        Жду
      </Button>,
    );
    expect(screen.queryByTestId('left')).not.toBeInTheDocument();
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('aria-busy', 'true');
    expect(btn).toBeDisabled();
  });

  describe('asChild', () => {
    it('single child <a>: рендерится <a>, не <button>', () => {
      render(
        <Button asChild>
          <a href="/x">Перейти</a>
        </Button>,
      );
      const link = screen.getByRole('link', { name: 'Перейти' });
      expect(link.tagName).toBe('A');
      expect(link).toHaveAttribute('href', '/x');
    });

    it('asChild + leftIcon: иконка попадает ВНУТРЬ <a>, без React #143', () => {
      render(
        <Button asChild leftIcon={<Icon testid="left" />}>
          <a href="/x">Перейти</a>
        </Button>,
      );
      const link = screen.getByRole('link', { name: 'Перейти' });
      const icon = screen.getByTestId('left');
      // Иконка должна быть внутри ссылки (контракт Slot+cloneElement).
      expect(link).toContainElement(icon);
    });

    it('asChild + rightIcon: иконка справа, внутри <a>', () => {
      render(
        <Button asChild rightIcon={<Icon testid="right" />}>
          <a href="/x">Дальше</a>
        </Button>,
      );
      const link = screen.getByRole('link', { name: 'Дальше' });
      expect(link).toContainElement(screen.getByTestId('right'));
    });

    it('asChild + loading: спиннер внутри <a>, leftIcon скрыт', () => {
      render(
        <Button asChild loading leftIcon={<Icon testid="left" />}>
          <a href="/x">Грузим</a>
        </Button>,
      );
      const link = screen.getByRole('link', { name: 'Грузим' });
      expect(link).toHaveAttribute('aria-busy', 'true');
      expect(screen.queryByTestId('left')).not.toBeInTheDocument();
    });

    it('asChild + Fragment children: распаковывается, не бросает (TD-005)', () => {
      // Это точный кейс из tech_debt_registry TD-005.
      // До фикса: Children.only бросал «React.Children.only expected to receive
      // a single React element child».
      render(
        <Button asChild>
          <>
            <a href="/y">Из Fragment</a>
          </>
        </Button>,
      );
      const link = screen.getByRole('link', { name: 'Из Fragment' });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/y');
    });

    it('asChild + Fragment + leftIcon: тоже не бросает', () => {
      render(
        <Button asChild leftIcon={<Icon testid="left" />}>
          <>
            <a href="/z">Test</a>
          </>
        </Button>,
      );
      expect(screen.getByRole('link', { name: 'Test' })).toBeInTheDocument();
      expect(screen.getByTestId('left')).toBeInTheDocument();
    });
  });
});
