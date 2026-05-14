import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'n8n-nodes-brikko — community-нода для n8n · Documentation',
  description:
    'Установи n8n-nodes-brikko через npm — получишь 4 ноды для маскирования ПДн (Anonymize, Restore, Chat, Detect PII) и 152-ФЗ-комплаентный workflow без отправки реальных данных в OpenAI/Anthropic.',
  alternates: { canonical: '/docs/integrations/n8n' },
};

const NPM_INSTALL = `# В корне n8n self-hosted установки:
npm install n8n-nodes-brikko

# или через Docker volume (если n8n в контейнере):
# смонтируй ~/.n8n/custom и поставь пакет туда
docker exec -it n8n npm install n8n-nodes-brikko --prefix ~/.n8n/custom`;

const ANONYMIZE_EXAMPLE = `// 1. Анонимизация перед отправкой в LLM
{
  "node": "Brikko Anonymize",
  "input": { "text": "Договор с Ивановым И.И., ИНН 770708389772" },
  "output": {
    "text": "Договор с <NAME_001>, ИНН <INN_001>",
    "mapping_id": "map_a3f9..."
  }
}

// 2. Передача замаскированного текста в любую LLM
//    (OpenAI / Anthropic / YandexGPT — любой узел n8n)

// 3. Восстановление в ответе
{
  "node": "Brikko Restore",
  "input": { "text": "<ответ LLM>", "mapping_id": "map_a3f9..." },
  "output": { "text": "<ответ с реальными именами>" }
}`;

const NODES = [
  {
    name: 'Brikko Anonymize',
    purpose: 'Маскирует PII перед LLM',
    detail:
      'Заменяет ФИО/ИНН/СНИЛС/ОГРН/паспорт/телефон/email/счёт на плейсхолдеры (`<NAME_001>`, `<INN_001>` и т.д.). Возвращает masked-текст + `mapping_id` для последующего restore.',
  },
  {
    name: 'Brikko Restore',
    purpose: 'Обратная операция',
    detail:
      'Принимает ответ LLM + `mapping_id`. Восстанавливает реальные значения. Mapping живёт 24 часа в Redis на брикко.ру — после TTL восстановить нельзя.',
  },
  {
    name: 'Brikko Chat',
    purpose: 'Anonymize + LLM + Restore в одном узле',
    detail:
      'Полный pipeline: автоматически маскирует промт, отправляет в Brikko Gateway (Claude/GPT/Gemini/YandexGPT/GigaChat/DeepSeek), восстанавливает ответ. Удобно для линейных workflow.',
  },
  {
    name: 'Brikko Detect PII',
    purpose: 'Сканер без модификации текста',
    detail:
      'Возвращает категории и счётчики обнаруженного PII без замены. Используй для audit-gates: блокировать workflow если в данных есть ПДн, или для метрик compliance.',
  },
];

export default function N8nDocPage() {
  return (
    <article className="brikko-docs-article">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация · Интеграции
        </p>
        <h1 className="brikko-h1 mt-3">n8n-nodes-brikko</h1>
        <p className="brikko-lede mt-4">
          Community-нода для n8n: 4 узла для anonymize/restore/chat/detect-PII в
          workflow. Подходит для команд, работающих по 152-ФЗ — реальные ПДн
          никогда не уходят в OpenAI или Anthropic, только маскированные
          плейсхолдеры.
        </p>
      </header>

      <section id="install" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Установка</h2>
        <p className="brikko-prose mt-4">
          Пакет опубликован в npm как <code>n8n-nodes-brikko</code>. Установка
          в n8n self-hosted:
        </p>
        <CodeBlock label="npm" code={NPM_INSTALL} />
        <p className="brikko-prose mt-4">
          После установки перезапусти n8n — узлы появятся в палитре под
          категорией «Brikko». В n8n Cloud (managed): community-узлы пока не
          поддерживаются — используй self-hosted.
        </p>
      </section>

      <section id="nodes" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Что внутри</h2>
        <ul className="mt-6 space-y-4">
          {NODES.map((n) => (
            <li key={n.name} className="brikko-card-flat">
              <p className="font-semibold text-fg-primary">{n.name}</p>
              <p className="mt-1 text-body-sm text-fg-muted">{n.purpose}</p>
              <p className="mt-2 text-body-sm">{n.detail}</p>
            </li>
          ))}
        </ul>
      </section>

      <section id="example" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Пример workflow</h2>
        <p className="brikko-prose mt-4">
          Стандартный 3-шаговый pipeline: анонимизация → вызов LLM → восстановление.
          Между шагами 1 и 3 любой узел n8n может работать с замаскированным текстом
          (OpenAI, Anthropic, HTTP request, Switch, Code, …) — реальные ПДн в этой
          цепочке нигде не появляются.
        </p>
        <CodeBlock label="3 шага в workflow" code={ANONYMIZE_EXAMPLE} />
      </section>

      <section id="links" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Ссылки</h2>
        <ul className="mt-6 space-y-2 brikko-prose">
          <li>
            <a
              href="https://www.npmjs.com/package/n8n-nodes-brikko"
              target="_blank"
              rel="noopener noreferrer"
              className="brikko-link"
            >
              n8n-nodes-brikko на npm →
            </a>
          </li>
          <li>
            <a
              href="https://github.com/brikkoAI/n8n-nodes-brikko"
              target="_blank"
              rel="noopener noreferrer"
              className="brikko-link"
            >
              Исходники + полный README на GitHub →
            </a>
          </li>
          <li>
            <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
              API: /v1/anonymize и /v1/restore (то, что нода вызывает под капотом) →
            </Link>
          </li>
        </ul>
      </section>

      <section id="next" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Что дальше</h2>
        <ul className="mt-6 grid gap-4 md:grid-cols-2">
          <li className="h-full">
            <Link
              href={'/integrations' as Route}
              className="brikko-card-link group h-full"
            >
              <h3 className="text-lg font-semibold text-fg-primary">
                Все интеграции
              </h3>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                Cursor, Claude Code, OpenAI Codex, GitHub Copilot, Gemini CLI —
                готовые инструкции под IDE и no-code инструменты.
              </p>
              <span className="mt-4 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                Посмотреть список
                <ArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </span>
            </Link>
          </li>
          <li className="h-full">
            <Link
              href={'/docs/concepts/privacy-v2' as Route}
              className="brikko-card-link group h-full"
            >
              <h3 className="text-lg font-semibold text-fg-primary">
                Privacy v2 (PII)
              </h3>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                Как именно работает маскинг: алгоритмы checksum-валидации,
                TTL mapping&rsquo;а, audit-логи, что не маскируется и почему.
              </p>
              <span className="mt-4 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                Открыть
                <ArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </span>
            </Link>
          </li>
        </ul>
      </section>
    </article>
  );
}
