'use client';

import Link from 'next/link';
import { Copy, Check } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import { BRAND } from '@/lib/brand';

/**
 * QuickStart — curl/TypeScript-сниппет на главной dashboard.
 *
 * Sprint 12 §1 фикс welcome flow (PRD 29 §2):
 *   - `apiKey` prop теперь pre-fills сниппет с реальным ключом, если передан.
 *     Раньше всегда был placeholder `sk-brk-XXXX` — юзер должен был сам копировать
 *     ключ из CreateKeyDialog в curl, лишний шаг.
 *   - Добавлен tab «TypeScript» рядом с curl — самый частый язык у нашей ICP
 *     (B2B SaaS / agencies). Python оставляем в /docs/quickstart, чтобы не раздувать
 *     карточку на overview-странице.
 *
 * UX-обоснование двух табов (а не четырёх как в `marketing/CodeSnippetTabs`):
 *   на главной dashboard юзер уже зарегистрирован — задача показать «вот твой
 *   готовый код», а не продать выбор языка. 2 таба = 90% наших юзеров (curl
 *   для smoke-теста + TS для реального проекта). Полный набор — на /docs.
 */

type TabKey = 'curl' | 'ts';

const PLACEHOLDER = 'sk-brk-XXXXXXXXXXXX';

const buildCurl = (apiKey: string) =>
  `curl https://${BRAND.apiDomain}/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto:cheap",
    "messages": [{"role": "user", "content": "Привет"}]
  }'`;

const buildTs = (apiKey: string) =>
  `import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: '${apiKey}',
  baseURL: 'https://${BRAND.apiDomain}/v1',
});

const response = await client.chat.completions.create({
  model: 'auto:cheap',
  messages: [{ role: 'user', content: 'Привет' }],
});

console.log(response.choices[0].message.content);`;

interface QuickStartProps {
  /** Если задан — показываем реальный ключ. Иначе — placeholder и CTA «Создать ключ». */
  apiKey?: string;
}

export function QuickStart({ apiKey }: QuickStartProps) {
  const [active, setActive] = useState<TabKey>('curl');
  const [copied, setCopied] = useState(false);

  const effectiveKey = apiKey ?? PLACEHOLDER;
  const snippet = active === 'curl' ? buildCurl(effectiveKey) : buildTs(effectiveKey);

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      toast.success('Скопировано');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Не удалось скопировать');
    }
  }

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-6">
      <header className="mb-3">
        <h2 className="text-lg font-semibold text-gray-900">Подключиться за 30 секунд</h2>
        <p className="mt-1 text-body-sm text-gray-500">
          {apiKey
            ? 'Ключ уже подставлен. Скопируй сниппет и запусти — получишь ответ от модели.'
            : 'Создай ключ — мы подставим его в curl автоматически.'}
        </p>
      </header>

      <div className="mb-3 flex gap-1 border-b border-gray-200" role="tablist" aria-label="Язык сниппета">
        {(['curl', 'ts'] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={active === key}
            aria-controls={`quickstart-panel-${key}`}
            onClick={() => setActive(key)}
            className={`relative px-3 py-2 text-body-sm font-medium transition-colors ${
              active === key ? 'text-brand-700' : 'text-gray-500 hover:text-gray-900'
            }`}
            data-testid={`quickstart-tab-${key}`}
          >
            {key === 'curl' ? 'curl' : 'TypeScript'}
            {active === key ? (
              <span aria-hidden="true" className="absolute inset-x-2 -bottom-px h-0.5 bg-brand-600" />
            ) : null}
          </button>
        ))}
      </div>

      <div className="relative" role="tabpanel" id={`quickstart-panel-${active}`}>
        <pre
          aria-label={`${active === 'curl' ? 'curl' : 'TypeScript'}-сниппет для первого запроса`}
          className="overflow-x-auto rounded-md bg-gray-900 p-4 text-code text-white"
        >
          <code>{snippet}</code>
        </pre>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={copy}
          leftIcon={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          className="absolute right-3 top-3 h-7 px-2"
          aria-label="Скопировать сниппет"
        >
          {copied ? 'Скопировано' : 'Скопировать'}
        </Button>
      </div>

      {!apiKey ? (
        <Button asChild className="mt-4" size="md">
          <Link href="/app/keys">Создать ключ</Link>
        </Button>
      ) : (
        <p className="mt-4 text-body-sm text-gray-500">
          Запусти команду в терминале — получишь ответ от модели.
        </p>
      )}
    </section>
  );
}
