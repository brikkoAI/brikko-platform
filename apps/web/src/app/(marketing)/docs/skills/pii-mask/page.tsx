import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'Brikko PII Skill — для OpenClaw / Claude Code / Codex · Documentation',
  description:
    'Skill для AI-агентов, который автоматически маскирует ФИО/ИНН/СНИЛС/ОГРН/паспорт/телефон в тексте перед отправкой в LLM и восстанавливает плейсхолдеры в ответе. False-positive ratio < 1% за счёт checksum-валидации российских ID.',
  alternates: { canonical: '/docs/skills/pii-mask' },
};

const CLAWHUB_INSTALL = `clawhub install brikkoAI/brikko-pii-skill
# или установка из Git напрямую (без ClawHub):
git clone https://github.com/brikkoAI/brikko-pii-skill.git \\
  ~/.openclaw/skills/brikko-pii-mask`;

const USAGE = `# В Claude Code или OpenClaw — skill сработает автоматически,
# когда агент видит в задаче персональные данные:

You> Сделай саммари переписки с клиентом
       (текст: "Иванов И.И. (ИНН 770708389772) просил скидку...")

Agent> [skill brikko-pii-mask активирован]
       [маскирую: <NAME_001> (ИНН <INN_001>) → отправляю в LLM]
       [получаю ответ от Claude/GPT/...]
       [восстанавливаю плейсхолдеры → отдаю тебе финальный текст]`;

const DETECTS = [
  ['ФИО', 'Иванов / Иванову / Ивановой → один placeholder', 'natasha NER + морфология'],
  ['ИНН', '10 цифр (юрлицо) или 12 (физлицо/ИП)', 'checksum ФНС'],
  ['СНИЛС', 'XXX-XXX-XXX YY', 'checksum ПФР'],
  ['ОГРН / ОГРНИП', '13 / 15 цифр', 'checksum ФНС'],
  ['Паспорт РФ', 'серия 4 + номер 6', 'regex (нет checksum в спеке)'],
  ['Телефон РФ', '+7 999, 8(495), 8 812 …', 'формат + 11 цифр'],
  ['Email', 'RFC 5322', 'regex'],
  ['Банковский счёт', '20 цифр юрлица', 'regex'],
  ['IPv4', '0.0.0.0–255.255.255.255', 'regex'],
];

export default function PiiSkillDocPage() {
  return (
    <article className="brikko-docs-article">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация · CLI и инструменты
        </p>
        <h1 className="brikko-h1 mt-3">Brikko PII Skill</h1>
        <p className="brikko-lede mt-4">
          Готовый Skill для AI-агентов (Claude Code, OpenClaw, Codex), который
          маскирует ПДн перед отправкой в LLM и восстанавливает плейсхолдеры в
          ответе. Drop-in замена самописного masking-слоя — ставится одной
          командой.
        </p>
      </header>

      <section id="install" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Установка</h2>
        <CodeBlock label="ClawHub" code={CLAWHUB_INSTALL} />
        <p className="brikko-prose mt-4">
          После установки skill автоматически активируется в OpenClaw / Claude Code,
          когда агент видит в задаче персональные данные. Для Codex —
          путь к skill&rsquo;у указывается в <code>.codex/skills/</code>.
        </p>
      </section>

      <section id="how" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Как работает</h2>
        <p className="brikko-prose mt-4">
          Skill встраивается в LLM-вызов как pre/post-обработчик. Перед отправкой
          текста в Claude / GPT / Gemini / YandexGPT — вызывает{' '}
          <code>POST /v1/anonymize</code> на api.brikko.ru, получает masked-текст
          и mapping ID. После ответа LLM — вызывает{' '}
          <code>POST /v1/restore</code> для возврата реальных значений. Mapping
          живёт 24 часа в Redis, после TTL — необратим.
        </p>
        <CodeBlock label="Пример работы агента" code={USAGE} />
      </section>

      <section id="detects" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Что детектируется</h2>
        <p className="brikko-prose mt-4">
          False-positive ratio &lt; 1% за счёт checksum-валидации российских ID.
          Это значит, что случайные 12-значные числа (timestamps, hashes,
          internal PK) не считаются ИНН — только реальные.
        </p>
        <ul className="mt-6 space-y-3">
          {DETECTS.map(([entity, pattern, validation]) => (
            <li key={entity} className="brikko-card-flat">
              <p className="font-semibold text-fg-primary">{entity}</p>
              <p className="mt-1 text-body-sm text-fg-muted">{pattern}</p>
              <p className="mt-1 text-body-sm text-fg-faint">
                Валидация: {validation}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section id="links" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Ссылки</h2>
        <ul className="mt-6 space-y-2 brikko-prose">
          <li>
            <a
              href="https://github.com/brikkoAI/brikko-pii-skill"
              target="_blank"
              rel="noopener noreferrer"
              className="brikko-link"
            >
              brikko-pii-skill на GitHub →
            </a>
          </li>
          <li>
            <a
              href="https://github.com/brikkoAI/brikko-pii-skill/releases/tag/v0.1.0"
              target="_blank"
              rel="noopener noreferrer"
              className="brikko-link"
            >
              v0.1.0 release notes →
            </a>
          </li>
          <li>
            <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
              Низкоуровневый API: /v1/anonymize · /v1/restore →
            </Link>
          </li>
          <li>
            <Link href={'/docs/concepts/privacy-v2' as Route} className="brikko-link">
              Privacy v2: алгоритмы маскинга подробно →
            </Link>
          </li>
        </ul>
      </section>

      <section id="next" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Что дальше</h2>
        <ul className="mt-6 grid gap-4 md:grid-cols-2">
          <li className="h-full">
            <Link
              href={'/docs/integrations/n8n' as Route}
              className="brikko-card-link group h-full"
            >
              <h3 className="text-lg font-semibold text-fg-primary">
                n8n-nodes-brikko
              </h3>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                Та же логика, но как community-нода для no-code
                workflow-инструмента n8n.
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
          <li className="h-full">
            <Link
              href={'/docs/cli' as Route}
              className="brikko-card-link group h-full"
            >
              <h3 className="text-lg font-semibold text-fg-primary">
                brikko-cli
              </h3>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                Если нужен CLI для маскирования из терминала, без AI-агента —
                команды brikko anonymize / restore / chat.
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
