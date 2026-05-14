/**
 * <McpPromptCopyBlock> — copy-paste promt-блок для /mcp лендинга (MCP S4).
 *
 * Покрываем критические кейсы:
 *   1. Рендерит prompt и кнопку «Скопировать».
 *   2. Click → navigator.clipboard.writeText вызван с точным prompt'ом.
 *   3. После copy кнопка показывает «Скопировано ✓» и возвращается обратно через 2s.
 *   4. Если clipboard API отсутствует → fallback на document.execCommand('copy').
 *   5. <pre> с prompt'ом focusable (tabIndex=0) — для keyboard-чтения.
 *
 * Регрессия, которую тесты ловят:
 *   - Что copy-button не «застрял» в copied=true (timer не отстреливает).
 *   - Что текст промпта не модифицирован (важно — promo-промпт согласован с CEO).
 *   - Что нет crash на insecure context (когда navigator.clipboard undefined).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { McpPromptCopyBlock } from '@/components/marketing/McpPromptCopyBlock';

const SAMPLE_PROMPT =
  'Hi. Clone https://github.com/brikkoAI/brikko-helper, ask me for token.';

describe('<McpPromptCopyBlock>', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // jsdom defines navigator.clipboard как getter-only — переопределяем
    // через Object.defineProperty.
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('рендерит prompt-текст в <pre> и кнопку «Скопировать»', () => {
    render(<McpPromptCopyBlock prompt={SAMPLE_PROMPT} />);

    // Точный promt виден в DOM (важно для CEO — текст согласован).
    expect(screen.getByText(SAMPLE_PROMPT)).toBeInTheDocument();

    // Кнопка с label по умолчанию.
    const btn = screen.getByTestId('mcp-prompt-copy-btn');
    expect(btn).toHaveAccessibleName('Скопировать промпт');
    expect(btn).toHaveTextContent('Скопировать');
  });

  it('по клику вызывает navigator.clipboard.writeText с точным prompt-текстом', async () => {
    render(<McpPromptCopyBlock prompt={SAMPLE_PROMPT} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId('mcp-prompt-copy-btn'));
    });

    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(SAMPLE_PROMPT);
  });

  it('после успешного copy кнопка меняется на «Скопировано ✓» и возвращается через 2s', async () => {
    render(<McpPromptCopyBlock prompt={SAMPLE_PROMPT} />);

    const btn = screen.getByTestId('mcp-prompt-copy-btn');
    expect(btn).toHaveTextContent('Скопировать');

    await act(async () => {
      fireEvent.click(btn);
      // Микрозадача clipboard.writeText промиса должна резолвиться.
      await Promise.resolve();
    });

    expect(btn).toHaveTextContent('Скопировано ✓');
    expect(btn).toHaveAccessibleName('Скопировано');

    // Прошло 2с — состояние сбрасывается.
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    expect(btn).toHaveTextContent('Скопировать');
  });

  it('<pre> focusable (tabIndex=0) — keyboard-доступен для чтения', () => {
    render(<McpPromptCopyBlock prompt={SAMPLE_PROMPT} />);
    const pre = screen.getByText(SAMPLE_PROMPT);
    expect(pre.tagName).toBe('PRE');
    expect(pre).toHaveAttribute('tabindex', '0');
  });

  it('кастомный label заголовка отображается в window-chrome', () => {
    render(
      <McpPromptCopyBlock prompt={SAMPLE_PROMPT} label="prompt for cursor" />,
    );
    expect(screen.getByText('prompt for cursor')).toBeInTheDocument();
  });

  it('если clipboard API недоступен → fallback на document.execCommand("copy"), без crash', async () => {
    // Сносим clipboard.
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: undefined,
    });
    // Mock execCommand на document.
    const execSpy = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      writable: true,
      value: execSpy,
    });

    render(<McpPromptCopyBlock prompt={SAMPLE_PROMPT} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId('mcp-prompt-copy-btn'));
      // Микрозадачи fallback'a (try/catch цепочка).
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(execSpy).toHaveBeenCalledWith('copy');
    expect(screen.getByTestId('mcp-prompt-copy-btn')).toHaveTextContent(
      'Скопировано ✓',
    );
  });
});
