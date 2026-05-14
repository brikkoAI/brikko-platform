import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'Getting Started — первый запрос за 5 минут · Brikko',
  description:
    'Пошаговое руководство: регистрация, создание API-ключа, первый запрос через Python / Node.js / curl / brikko-cli. Стартовые 200 ₽ welcome-бонусом.',
  alternates: { canonical: '/docs/getting-started' },
};

/**
 * /docs/getting-started — UX-обоснование структуры:
 *
 *   1) Eyebrow + h1 + лид: задаёт expectations за 3 секунды («это quick start,
 *      не reference»).
 *   2) Шаги пронумерованы и собраны в card-flat блоки. Каждый шаг = одна
 *      mini-цель с клиабельным CTA. Это снижает когнитивную нагрузку: «прочёл
 *      шаг — выполнил — следующий», как в onboarding-чеклистах Linear.
 *   3) Шаг 3 (первый запрос) — три tabs-equivalent code-block'а друг под
 *      другом. Не делаем UI-tabs (overkill для 3 вариантов): пользователь
 *      доскроллит до своего языка за 1 секунду. Stripe / Anthropic делают так.
 *   4) Финальная секция «Что дальше» — линки на reference-страницы. Это
 *      продолжение journey, без неё пользователь застрянет на «у меня всё
 *      работает, но что дальше?».
 *
 * Почему именно openai-SDK первым в примерах: в РФ-сегменте 70%+ кода уже
 * написано под openai SDK (это lingua franca dev-сообщества). Brikko's
 * value-prop = «смени base_url, остальное работает» — это надо показать
 * первой строкой кода, не второй.
 */

const PYTHON_EXAMPLE = `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-...",
    base_url="https://api.brikko.ru/v1",
)

resp = client.chat.completions.create(
    model="gpt-5.4-mini",
    messages=[{"role": "user", "content": "Привет, Brikko"}],
)

print(resp.choices[0].message.content)`;

const NODE_EXAMPLE = `import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: process.env.BRIKKO_API_KEY,
  baseURL: 'https://api.brikko.ru/v1',
});

const resp = await client.chat.completions.create({
  model: 'gpt-5.4-mini',
  messages: [{ role: 'user', content: 'Привет, Brikko' }],
});

console.log(resp.choices[0].message.content);`;

const CURL_EXAMPLE = `curl -sS https://api.brikko.ru/v1/chat/completions \\
  -H "Authorization: Bearer $BRIKKO_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [
      {"role": "user", "content": "Привет, Brikko"}
    ]
  }'`;

const CLI_EXAMPLE = `# Установка глобально:
npm install -g brikko-cli

# Авторизация (откроет браузер для login):
brikko login

# Первый запрос:
brikko chat "Привет, Brikko"`;

export default function GettingStartedPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация · Начало
        </p>
        <h1 className="brikko-h1 mt-3">Getting Started</h1>
        <p className="brikko-lede mt-4">
          Первый запрос к любой из 38 LLM через Brikko — за 5 минут. Если у вас
          уже есть код на OpenAI SDK, миграция занимает одну строку: смену{' '}
          <code className="brikko-code-inline">base_url</code>. Welcome-бонус 200 ₽
          даёт примерно 1 000 запросов к GPT-5.4 mini — карта не нужна.
        </p>
      </header>

      <section id="step-1" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">1. Регистрация</h2>
        <p className="brikko-prose mt-4">
          Создайте аккаунт на{' '}
          <Link href={'/signup' as Route} className="brikko-link">
            brikko.ru/signup
          </Link>
          . Email + пароль или Google OAuth. После подтверждения email на баланс
          автоматически зачисляется{' '}
          <strong className="font-semibold text-fg-primary">200 ₽ welcome-бонусом</strong>{' '}
          — это ~1 000 запросов к <code className="brikko-code-inline">gpt-5.4-mini</code>{' '}
          или ~70 к <code className="brikko-code-inline">claude-opus-4-7</code>.
          Никаких карт, никакой пробной подписки.
        </p>
      </section>

      <section id="step-2" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">2. Создать API-ключ</h2>
        <p className="brikko-prose mt-4">
          В личном кабинете —{' '}
          <Link href={'/app/keys' as Route} className="brikko-link">
            /app/keys
          </Link>{' '}
          — кнопка «Создать ключ». Ключ показывается{' '}
          <strong className="font-semibold text-fg-primary">один раз</strong> в
          момент создания, после этого Brikko хранит только хеш — мы физически
          не можем его восстановить. Скопируйте сразу в переменную окружения:
        </p>
        <CodeBlock label=".env" code={`BRIKKO_API_KEY=sk-brk-...`} />
        <p className="brikko-prose mt-5">
          Каждому ключу можно дать имя (
          <code className="brikko-code-inline">prod-backend</code>,{' '}
          <code className="brikko-code-inline">staging</code>,{' '}
          <code className="brikko-code-inline">cli-laptop</code>) — это удобно
          для аудита: в дашборде потребления вы видите расход по каждому ключу
          отдельно. Скомпрометированный ключ отзывается одним кликом, новый
          выпускается тут же — старый перестаёт работать мгновенно.
        </p>
      </section>

      <section id="step-3" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">3. Первый запрос</h2>
        <p className="brikko-prose mt-4">
          API совместим с OpenAI Chat Completions: тот же контракт, те же поля,
          те же streaming-чанки. Если у вас уже есть код на{' '}
          <code className="brikko-code-inline">openai</code> SDK — измените{' '}
          <code className="brikko-code-inline">base_url</code> и работайте
          дальше. Никаких новых библиотек устанавливать не нужно.
        </p>

        <div className="mt-6 space-y-6">
          <CodeBlock label="Python (openai SDK)" code={PYTHON_EXAMPLE} flush />
          <CodeBlock label="Node.js (openai SDK)" code={NODE_EXAMPLE} flush />
          <CodeBlock label="curl" code={CURL_EXAMPLE} flush />
        </div>

        <p className="brikko-prose mt-6">
          Альтернатива — наш CLI-инструмент{' '}
          <code className="brikko-code-inline">brikko-cli</code>: ставится одной
          командой, авторизуется через OAuth (без копирования ключей), отвечает
          в терминале без необходимости писать код:
        </p>

        <CodeBlock label="brikko-cli" code={CLI_EXAMPLE} />

        <p className="brikko-prose mt-5">
          Подробнее про CLI —{' '}
          <Link href={'/docs/cli' as Route} className="brikko-link">
            /docs/cli
          </Link>
          .
        </p>
      </section>

      <section id="auto" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Автовыбор модели</h2>
        <p className="brikko-prose mt-4">
          Если не хочется выбирать конкретную модель из 19 — передайте{' '}
          <code className="brikko-code-inline">model: &quot;auto:cheap&quot;</code>{' '}
          (или <code className="brikko-code-inline">auto:smart</code> /{' '}
          <code className="brikko-code-inline">auto:fast</code> /{' '}
          <code className="brikko-code-inline">auto:ru-legal</code>). Smart
          Router сам выберет самую дешёвую (умную / быструю / РФ-локальную)
          модель, способную справиться с конкретным запросом, и держит наготове
          fallback на случай сбоя провайдера. Подробнее —{' '}
          <Link href={'/docs/smart-routing' as Route} className="brikko-link">
            /docs/smart-routing
          </Link>
          .
        </p>
      </section>

      <section id="next" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Что дальше</h2>
        <p className="brikko-prose mt-4">
          Дальнейшие разделы — по нарастающей сложности. Если у вас работает
          первый запрос — выбирайте, что нужно прямо сейчас:
        </p>

        <ul className="mt-6 grid gap-4 md:grid-cols-2">
          <NextCard
            href={'/docs/api/chat-completions' as Route}
            title="Chat Completions API"
            description="Полный reference: streaming, function calling, JSON mode, заголовки PII-protect, error codes."
          />
          <NextCard
            href={'/docs/api/anonymize' as Route}
            title="Anonymize / Restore"
            description="Standalone PII-маскинг для безопасной отправки персональных данных в любой LLM."
          />
          <NextCard
            href={'/docs/concepts/privacy-v2' as Route}
            title="Privacy v2"
            description="Концепция PII-маскинга в Brikko: что детектируем, как, что НЕ детектируем, сравнение с Presidio."
          />
          <NextCard
            href={'/docs/cli' as Route}
            title="brikko-cli"
            description="11 команд, авторизация через OAuth, локальный agent для работы с ключами без копирования."
          />
        </ul>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс, карта не нужна.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}

function NextCard({
  href,
  title,
  description,
}: {
  href: Route;
  title: string;
  description: string;
}) {
  return (
    <li className="h-full">
      <Link href={href} className="brikko-card-link group h-full">
        <h3 className="text-lg font-semibold text-fg-primary">{title}</h3>
        <p className="mt-2 flex-1 text-body-sm text-fg-muted">{description}</p>
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
  );
}
