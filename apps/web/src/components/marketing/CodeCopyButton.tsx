'use client';

/**
 * CodeCopyButton — inline command + copy-to-clipboard для marketing-страниц
 * (Sprint 13.7, 2026-05-05).
 *
 * Используется в Studio anchor-блоке на главной: показывает curl-команду
 * установки + кнопку копирования. Минимальный JS bundle (нет deps),
 * keyboard-accessible (button + aria-live для feedback).
 *
 * UX:
 *   - Mono-стэк, espresso bg (как code-window в Hero).
 *   - На copy: кнопка переключается на «Скопировано» на 2s, aria-live polite.
 *   - prefers-reduced-motion respected (transitions через CSS-vars).
 */

import { useCallback, useState } from 'react';

interface Props {
  command: string;
}

export function CodeCopyButton({ command }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(command);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      }
    } catch {
      // Clipboard API недоступен (insecure context / sandboxed) — fallback
      // на legacy execCommand.
      try {
        const textarea = document.createElement('textarea');
        textarea.value = command;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      } catch {
        // Silently fail — пользователь увидит код и сможет скопировать вручную.
      }
    }
  }, [command]);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: 0,
        background: 'var(--code-bg)',
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        overflow: 'hidden',
        maxWidth: '100%',
      }}
    >
      <code
        style={{
          flex: 1,
          padding: '12px 16px',
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 13,
          lineHeight: 1.5,
          color: 'var(--code-fg)',
          whiteSpace: 'nowrap',
          overflow: 'auto',
          scrollbarWidth: 'none',
        }}
      >
        $ {command}
      </code>
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? 'Скопировано' : 'Скопировать команду'}
        style={{
          flexShrink: 0,
          padding: '0 16px',
          background: 'rgba(245, 245, 244, 0.06)',
          border: 0,
          borderLeft: '1px solid rgba(245, 245, 244, 0.08)',
          color: 'var(--code-fg)',
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 11,
          fontWeight: 500,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          cursor: 'pointer',
          transition: 'background 200ms var(--ease-in-out-quart)',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'rgba(245, 245, 244, 0.12)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'rgba(245, 245, 244, 0.06)';
        }}
      >
        <span aria-live="polite">{copied ? 'OK' : 'Copy'}</span>
      </button>
    </div>
  );
}
