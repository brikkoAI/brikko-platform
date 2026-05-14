'use client';

/**
 * CodeBlock — multi-line code-fence с label, copy-button, theme-aware фоном.
 *
 * Отличия от существующего <CodeCopyButton>:
 *   - <CodeCopyButton> — single-line, для inline-команд в Hero/Studio.
 *   - <CodeBlock> — multi-line с переносами, для reference-доки.
 *
 * UX-обоснование:
 *   - copy-button в правом верхнем углу — стандарт (Stripe, Vercel, GitHub).
 *     Это первое место, куда тянется глаз разработчика. Меняется на «✓» на 2с
 *     после успешного копирования (visual feedback, NN/g принцип).
 *   - Label сверху (например, «Python», «curl», «TypeScript») — позволяет
 *     отличать варианты одного и того же примера на разных языках без
 *     счёта строк. Опционален.
 *   - Mono-шрифт + espresso-bg даже в light-теме (как в Cream Studio v6) —
 *     создаёт визуальный «срез» в IDE. В dark-теме — наоборот, cream-bg
 *     (см. CSS-vars --code-bg / --code-fg).
 *   - aria-live="polite" на статусе копирования — screen-reader сообщает
 *     «Скопировано», не перебивая пользователя (a11y).
 *   - Никакого syntax-highlighting в MVP (требование задачи) — Prism/Shiki
 *     это V2. Plain mono-text читается отлично, разработчики привыкли.
 */

import { useCallback, useState } from 'react';

interface Props {
  code: string;
  /** Optional label сверху ("Python", "curl", etc.) */
  label?: string;
  /** Если true — рендерим без обёртки .mt-6, для случаев когда отступ задаётся внешне. */
  flush?: boolean;
}

export function CodeBlock({ code, label, flush }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(code);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
        return;
      }
    } catch {
      // fall through to legacy
    }
    try {
      const ta = document.createElement('textarea');
      ta.value = code;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Silent fail — пользователь может скопировать вручную.
    }
  }, [code]);

  return (
    <div className={flush ? 'brikko-docs-codeblock' : 'brikko-docs-codeblock mt-6'}>
      <div className="brikko-docs-codeblock-header">
        {label ? (
          <span className="brikko-docs-codeblock-label">{label}</span>
        ) : (
          <span aria-hidden="true" />
        )}
        <button
          type="button"
          onClick={handleCopy}
          className="brikko-docs-codeblock-copy"
          aria-label={copied ? 'Скопировано' : 'Скопировать код'}
        >
          <span aria-live="polite">{copied ? 'OK' : 'Copy'}</span>
        </button>
      </div>
      <div className="brikko-code-shell">
        <pre className="brikko-code-pre">
          <code className="font-mono">{code}</code>
        </pre>
      </div>
    </div>
  );
}
