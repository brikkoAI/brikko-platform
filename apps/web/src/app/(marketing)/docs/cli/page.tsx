import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'brikko-cli — командная строка для Brikko Studio · Documentation',
  description:
    'CLI для управления локальным Brikko Studio + чатиться/маскировать ПД из терминала через api.brikko.ru. 13 команд, npm-пакет, кросс-платформенный (Linux/macOS/Windows-WSL).',
  alternates: { canonical: '/docs/cli' },
};

/**
 * /docs/cli — UX-обоснование структуры:
 *
 *   1) Hero — install-команда сразу под h1.
 *   2) §1 Установка — npm primary, curl-bash как fallback для машин без Node.
 *   3) §2 Что делает — короткий лид + 2 группы команд (Studio lifecycle + API).
 *   4) §3 Quick examples — chat / anonymize / safe-chat / studio init.
 *   5) §4 Альтернативная установка — bash-installer (для тех у кого нет Node).
 *   6) §5 Что дальше — ссылки на API reference и related docs.
 */

const STUDIO_COMMANDS = [
  {
    cmd: 'brikko init',
    purpose: 'Bootstrap локального Brikko Studio',
    notes:
      'Скачивает docker-compose, создаёт .env, делает docker pull, поднимает 3 контейнера, открывает браузер на http://localhost:3737.',
  },
  {
    cmd: 'brikko start',
    purpose: 'Запустить Studio',
    notes: 'docker compose up -d. После init вызывается автоматически.',
  },
  {
    cmd: 'brikko stop',
    purpose: 'Остановить контейнеры (volumes сохраняются)',
    notes: 'docker compose stop. Данные остаются — start вернёт всё на место.',
  },
  {
    cmd: 'brikko down',
    purpose: 'Стоп + удаление контейнеров',
    notes:
      'docker compose down. Volumes сохраняются. Удобно перед обновлением или если что-то висит.',
  },
  {
    cmd: 'brikko status',
    purpose: 'Состояние сервисов + healthcheck',
    notes:
      'Печатает таблицу: container / status / image. С --json — machine-readable вывод для CI.',
  },
  {
    cmd: 'brikko logs [service]',
    purpose: 'Логи контейнеров',
    notes:
      'docker compose logs. Без аргумента — все сервисы. С --follow — стрим в realtime. --tail N для последних N строк.',
  },
  {
    cmd: 'brikko restart [service]',
    purpose: 'Перезапуск сервиса',
    notes:
      'Без аргумента — перезапускает все. Полезно после смены .env или при странном поведении.',
  },
  {
    cmd: 'brikko update',
    purpose: 'docker pull + recreate контейнеры',
    notes:
      'Обновляет images до последней версии тега из .env. Без потери volumes и .env.',
  },
  {
    cmd: 'brikko uninstall',
    purpose: 'Полное удаление (DESTRUCTIVE)',
    notes:
      'Стоп + удаление контейнеров + удаление volumes + удаление install-папки. Спрашивает подтверждение, --yes пропустить.',
  },
  {
    cmd: 'brikko version',
    purpose: 'Версии всех компонентов',
    notes:
      'CLI / Studio Core / Anonymizer / Docker / Compose. С --json — для CI и diagnostic-репортов.',
  },
  {
    cmd: 'brikko doctor',
    purpose: 'Диагностика установки',
    notes:
      'Проверяет: Docker daemon, Compose v2, занятость порта 3737, свободное место на диске, healthcheck endpoint. С --json — структурированный отчёт.',
  },
];

const API_COMMANDS = [
  {
    cmd: 'brikko chat <prompt>',
    purpose: 'Чат-запрос через api.brikko.ru',
    notes:
      'Печатает ответ. Флаги: --model auto:cheap|auto:smart, --stream (SSE), --json, --system "...". Читает prompt из stdin при -.',
  },
  {
    cmd: 'brikko anonymize',
    purpose: 'Маскировка ПД (POST /v1/anonymize)',
    notes:
      'Текст из --text или stdin → JSON {masked_text, mapping_id, count, audit}. С --pretty — таблица.',
  },
  {
    cmd: 'brikko restore --mapping-id <id>',
    purpose: 'Восстановление плейсхолдеров',
    notes:
      'Текст с плейсхолдерами на stdin → восстановленный текст в stdout. mapping_id из anonymize.',
  },
  {
    cmd: 'brikko safe-chat <prompt>',
    purpose: 'Mask → chat → restore в одной команде',
    notes:
      'Anonymize prompt → отправить в /v1/chat/completions → restore ответа. Для compliance: один вызов вместо трёх.',
  },
];

const NPM_INSTALL = `npm install -g brikko-cli
brikko init`;

const CHAT_EXAMPLE = `# Чат через auto-router (DeepSeek, ~3 ₽/1k токенов):
brikko chat "Объясни TLS handshake простыми словами"

# С конкретной моделью + JSON:
brikko chat "Извлеки JSON из текста: ..." \\
  --model auto:smart \\
  --json

# Streaming:
brikko chat "Напиши длинный ответ" --stream`;

const SAFE_CHAT_EXAMPLE = `# Безопасный чат: ПД маскируются автоматически
echo "Письмо клиенту Иванову, ИНН 7707083893, на 150 000 ₽" \\
  | brikko safe-chat
# 1. anonymize → "Письмо клиенту <NAME_1>, ИНН <INN_1>, на 150 000 ₽"
# 2. POST /v1/chat/completions с masked текстом
# 3. restore ответа LLM → реальные «Иванов» и «7707083893» возвращаются
# stdout: финальный текст с восстановленными ПД`;

const ANONYMIZE_PIPELINE_EXAMPLE = `# Если LLM свой, не наш — маскируй вручную:
RES=$(echo "ИНН 7707083893" | brikko anonymize)
MASKED=$(echo "$RES" | jq -r .masked_text)
MAPPING=$(echo "$RES" | jq -r .mapping_id)

# Отправь masked в любой LLM (Claude, GPT, локальный)
ANSWER=$(your-llm-cli "$MASKED")

# Восстанови плейсхолдеры в ответе
echo "$ANSWER" | brikko restore --mapping-id "$MAPPING"`;

const STUDIO_INIT_EXAMPLE = `# Первая установка локального Studio:
brikko init
# → скачивает 3 контейнера (Studio Core + Anonymizer + Redis)
# → создаёт .env, поднимает docker-compose
# → открывает http://localhost:3737 в браузере

# Управление:
brikko status     # что запущено + healthcheck
brikko logs -f    # потоковые логи
brikko update     # обновиться до последней версии
brikko stop       # остановить, данные не теряются`;

const CURL_INSTALL = `curl -sSL https://install.brikko.ru/studio.sh | bash`;

export default function CliDocPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация · CLI
        </p>
        <h1 className="brikko-h1 mt-3">brikko-cli@0.3.0</h1>
        <p className="brikko-lede mt-4">
          Командная строка Brikko: одной утилитой управляешь локальным Studio
          (docker-compose под капотом), чатишься с любой из 38 LLM через
          api.brikko.ru и маскируешь персональные данные перед отправкой.
          Кросс-платформенно: macOS, Linux, Windows + WSL.
        </p>
      </header>

      <section id="install" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Установка</h2>
        <p className="brikko-prose mt-4">
          Основной способ — через npm (требует Node 18+). Альтернатива —
          curl-installer (см.{' '}
          <a href="#alt-install" className="brikko-link">
            ниже
          </a>
          ) если нет Node.js.
        </p>
        <CodeBlock label="npm" code={NPM_INSTALL} />
        <p className="brikko-prose mt-5">
          После{' '}
          <code className="brikko-code-inline">brikko init</code> Studio будет
          доступен на{' '}
          <code className="brikko-code-inline">http://localhost:3737</code>.
          Чтобы пользоваться API-командами (chat / anonymize), задай ключ:
        </p>
        <CodeBlock
          label="API key"
          code={`export BRIKKO_API_KEY=sk-brk-xxxxxxxxxxxx`}
        />
      </section>

      <section id="commands-studio" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Команды управления Studio</h2>
        <p className="brikko-prose mt-4">
          Эти команды управляют локальным docker-compose стеком. Не требуют
          API-ключа — работают с твоей машиной.
        </p>

        <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
          <table className="w-full border-collapse text-body-sm">
            <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
              <tr>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Команда
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Назначение
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Заметки
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              {STUDIO_COMMANDS.map((c, i) => {
                const isLast = i === STUDIO_COMMANDS.length - 1;
                const cellClass = isLast
                  ? 'px-4 py-3 align-top'
                  : 'border-b border-[var(--hairline)] px-4 py-3 align-top';
                return (
                  <tr key={c.cmd}>
                    <td className={cellClass}>
                      <code className="font-mono text-body-sm font-semibold text-fg-primary">
                        {c.cmd}
                      </code>
                    </td>
                    <td className={cellClass}>{c.purpose}</td>
                    <td className={cellClass}>{c.notes}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section id="commands-api" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">API-команды</h2>
        <p className="brikko-prose mt-4">
          Работают с api.brikko.ru. Требуют{' '}
          <code className="brikko-code-inline">BRIKKO_API_KEY</code> в
          окружении (или флаг{' '}
          <code className="brikko-code-inline">--key</code>).
        </p>

        <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
          <table className="w-full border-collapse text-body-sm">
            <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
              <tr>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Команда
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Назначение
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Заметки
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              {API_COMMANDS.map((c, i) => {
                const isLast = i === API_COMMANDS.length - 1;
                const cellClass = isLast
                  ? 'px-4 py-3 align-top'
                  : 'border-b border-[var(--hairline)] px-4 py-3 align-top';
                return (
                  <tr key={c.cmd}>
                    <td className={cellClass}>
                      <code className="font-mono text-body-sm font-semibold text-fg-primary">
                        {c.cmd}
                      </code>
                    </td>
                    <td className={cellClass}>{c.purpose}</td>
                    <td className={cellClass}>{c.notes}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section id="quick" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Quick examples</h2>

        <h3 className="mt-6 text-base font-semibold text-fg-primary">
          Чат из терминала
        </h3>
        <p className="brikko-prose mt-3">
          Аналог{' '}
          <code className="brikko-code-inline">curl + jq</code>, но в одну
          команду. Подходит для скриптов и быстрых ad-hoc запросов.
        </p>
        <CodeBlock label="brikko chat" code={CHAT_EXAMPLE} />

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          safe-chat: чат с автоматической защитой ПД
        </h3>
        <p className="brikko-prose mt-3">
          Killer-feature для compliance: одна команда делает mask → chat →
          restore. ПД клиента остаются у тебя на машине, в LLM уходят только
          плейсхолдеры, ответ возвращается с реальными данными.
        </p>
        <CodeBlock label="brikko safe-chat" code={SAFE_CHAT_EXAMPLE} />

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          anonymize + restore: ручной pipeline
        </h3>
        <p className="brikko-prose mt-3">
          Если используешь свой LLM (не через api.brikko.ru) — маскируй и
          восстанавливай вручную:
        </p>
        <CodeBlock label="anonymize → llm → restore" code={ANONYMIZE_PIPELINE_EXAMPLE} />

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          Локальный Studio
        </h3>
        <p className="brikko-prose mt-3">
          Если нужно чтобы ПД вообще не покидали машину — поставь Studio
          локально. Полный self-host через docker-compose:
        </p>
        <CodeBlock label="brikko init" code={STUDIO_INIT_EXAMPLE} />
      </section>

      <section id="alt-install" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Альтернативная установка (без Node.js)</h2>
        <p className="brikko-prose mt-4">
          Если на машине нет Node.js — bash-installer работает на macOS, Linux
          и Windows + WSL. Скачивает docker-compose через JSDelivr CDN
          (стабильно в РФ без VPN), поднимает Studio.
        </p>
        <CodeBlock label="curl install" code={CURL_INSTALL} />
        <p className="brikko-prose mt-5">
          Скрипт детектирует WSL отдельно от Linux, ждёт Docker Desktop до 60
          секунд, при ошибке даёт actionable hints для каждой ОС. Никаких
          telemetry-вызовов в скрипте установки нет — код на{' '}
          <a
            href="https://github.com/brikkoAI/brikko-studio/blob/main/install.sh"
            target="_blank"
            rel="noopener noreferrer"
            className="brikko-link"
          >
            github.com/brikkoAI/brikko-studio
          </a>
          .
        </p>
      </section>

      <section id="next" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Что дальше</h2>
        <p className="brikko-prose mt-4">CLI закрывает 80% сценариев из терминала. Глубже:</p>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            <Link href={'/docs/api/chat-completions' as Route} className="brikko-link">
              Chat Completions API reference
            </Link>{' '}
            — полный контракт endpoint&apos;а.
          </li>
          <li>
            <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
              Anonymize API reference
            </Link>{' '}
            — детали /v1/anonymize и /v1/restore.
          </li>
          <li>
            <Link href={'/docs/concepts/privacy-v2' as Route} className="brikko-link">
              Privacy v2 — концепция защиты ПД
            </Link>{' '}
            — как работает masking и почему он надёжный.
          </li>
        </ul>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс. Создаётся за 30
          секунд на brikko.ru, дальше всё через CLI.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}
