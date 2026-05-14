'use client';

/**
 * McpPromptCopyBlock — многострочный промпт для Claude/Cursor с кнопкой
 * «Скопировать» (MCP S4 landing, /mcp).
 *
 * Отличие от CodeCopyButton:
 *   - CodeCopyButton рендерит one-liner-команду с `whiteSpace: nowrap` и
 *     горизонтальным скроллом (для curl-команды установки в Hero).
 *   - Этот блок рендерит ~5-строчный prompt-текст с переносами и кнопкой
 *     сверху справа (как в OpenAI/Anthropic docs).
 *
 * UX:
 *   - macOS-style window-chrome (3 dots + label «prompt.txt») —
 *     визуальная связка с Hero code-window.
 *   - Кнопка «Скопировать» в правом верхнем углу — стандартное место,
 *     не конкурирует с текстом за внимание.
 *   - На copy: «Скопировано ✓» на 2s, `aria-live="polite"` для screen-reader.
 *   - Fallback на `execCommand('copy')` если clipboard API недоступен
 *     (insecure context). Скопировано так же как в CodeCopyButton.
 *   - `prefers-reduced-motion` уважается (transition'ы через CSS-vars).
 *
 * a11y:
 *   - `<pre>` имеет `tabIndex={0}` чтобы юзер мог сфокусироваться и читать
 *     текст клавиатурой.
 *   - Кнопка `aria-label` отдельный для screen-reader.
 */

import { useCallback, useState } from 'react';

interface Props {
  prompt: string;
  /** Заголовок window-chrome (правый верх). Default — «prompt.txt». */
  label?: string;
}

export function McpPromptCopyBlock({ prompt, label = 'prompt.txt' }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(prompt);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
        return;
      }
      throw new Error('clipboard-unavailable');
    } catch {
      // Fallback на legacy execCommand (insecure context / sandboxed iframe).
      try {
        const textarea = document.createElement('textarea');
        textarea.value = prompt;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      } catch {
        // Тихо падаем — пользователь увидит prompt и скопирует мышью.
      }
    }
  }, [prompt]);

  return (
    <div
      data-testid="mcp-prompt-copy-block"
      style={{
        position: 'relative',
        background: 'var(--bg-tier-2)',
        borderRadius: 24,
        padding: 6,
        border: '1px solid var(--hairline)',
        boxShadow: '0 24px 48px -24px rgba(28, 25, 23, 0.18)',
      }}
    >
      <div
        style={{
          position: 'relative',
          background: 'var(--code-bg)',
          borderRadius: 18,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            padding: '12px 16px',
            borderBottom: '1px solid rgba(245, 245, 244, 0.08)',
          }}
        >
          <span style={dotStyle(0.18)} aria-hidden="true" />
          <span style={dotStyle(0.13)} aria-hidden="true" />
          <span style={dotStyle(0.09)} aria-hidden="true" />
          <span
            style={{
              marginLeft: 12,
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 11,
              color: 'var(--code-accent)',
              letterSpacing: '0.04em',
            }}
          >
            {label}
          </span>
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? 'Скопировано' : 'Скопировать промпт'}
            data-testid="mcp-prompt-copy-btn"
            style={{
              marginLeft: 'auto',
              padding: '6px 14px',
              background: copied
                ? 'rgba(245, 245, 244, 0.18)'
                : 'rgba(245, 245, 244, 0.06)',
              border: '1px solid rgba(245, 245, 244, 0.10)',
              borderRadius: 8,
              color: 'var(--code-fg)',
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 11,
              fontWeight: 500,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              cursor: 'pointer',
              transition: 'background 200ms var(--ease-in-out-quart)',
            }}
            onMouseEnter={(e) => {
              if (!copied)
                e.currentTarget.style.background =
                  'rgba(245, 245, 244, 0.12)';
            }}
            onMouseLeave={(e) => {
              if (!copied)
                e.currentTarget.style.background =
                  'rgba(245, 245, 244, 0.06)';
            }}
          >
            <span aria-live="polite">{copied ? 'Скопировано ✓' : 'Скопировать'}</span>
          </button>
        </div>
        <pre
          tabIndex={0}
          style={{
            margin: 0,
            padding: '20px 22px 24px',
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 13,
            lineHeight: 1.65,
            color: 'var(--code-fg)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 'min(50vh, 360px)',
            overflowY: 'auto',
            outline: 'none',
          }}
        >
          {prompt}
        </pre>
      </div>
    </div>
  );
}

function dotStyle(opacity: number): React.CSSProperties {
  return {
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: `rgba(245, 245, 244, ${opacity})`,
  };
}
