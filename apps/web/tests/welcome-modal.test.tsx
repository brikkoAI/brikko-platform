/**
 * <WelcomeModal> — §3.1, показывается ровно один раз.
 *
 * Что покрываем:
 *   - При первом маунте (нет localStorage flag) — модалка открыта.
 *   - При уже dismissed — не открывается.
 *   - Клик «Пропустить» — ставит flag и закрывает.
 *   - Клик «Начать» — ставит flag, закрывает, вызывает onStart.
 *   - Тексты дословно из §3.1.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WelcomeModal } from '@/components/dashboard/WelcomeModal';

const STORAGE_KEY = 'brikko.welcome_dismissed';

describe('<WelcomeModal>', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('первый маунт — модалка открыта с R-vetted текстом', async () => {
    render(<WelcomeModal />);
    expect(await screen.findByText('Добро пожаловать в Brikko')).toBeInTheDocument();
    expect(
      screen.getByText('Три шага до первого запроса. Займёт 5 минут.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Создать API-ключ')).toBeInTheDocument();
    expect(screen.getByText('Сделать первый запрос')).toBeInTheDocument();
    expect(screen.getByText('Пополнить баланс')).toBeInTheDocument();
  });

  it('если localStorage уже dismissed — модалка не открывается', () => {
    window.localStorage.setItem(STORAGE_KEY, 'true');
    render(<WelcomeModal />);
    expect(screen.queryByText('Добро пожаловать в Brikko')).toBeNull();
  });

  it('Пропустить — ставит flag и закрывает', async () => {
    const user = userEvent.setup();
    render(<WelcomeModal />);
    await user.click(screen.getByTestId('welcome-modal-skip'));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('true');
    expect(screen.queryByText('Добро пожаловать в Brikko')).toBeNull();
  });

  it('Начать — ставит flag, закрывает, вызывает onStart', async () => {
    const onStart = vi.fn();
    const user = userEvent.setup();
    render(<WelcomeModal onStart={onStart} />);
    await user.click(screen.getByTestId('welcome-modal-start'));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('true');
    expect(onStart).toHaveBeenCalledOnce();
    expect(screen.queryByText('Добро пожаловать в Brikko')).toBeNull();
  });
});
