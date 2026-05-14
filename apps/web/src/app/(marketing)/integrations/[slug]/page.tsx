import Link from 'next/link';
import type { Route } from 'next';
import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import {
  getIntegration,
  getIntegrationSlugs,
  type Integration,
} from '@/lib/integrations';

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  return getIntegrationSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const integration = getIntegration(slug);
  if (!integration) {
    return { title: 'Интеграция не найдена · Brikko' };
  }
  return {
    title: `${integration.title} · Brikko`,
    description: integration.metaDescription,
    alternates: { canonical: `/integrations/${integration.slug}` },
  };
}

function formatRub(value: number): string {
  return `${value.toLocaleString('ru-RU')} ₽`;
}

export default async function IntegrationPage({ params }: PageProps) {
  const { slug } = await params;
  const integration = getIntegration(slug);
  if (!integration) notFound();

  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <Link
        href={'/integrations' as Route}
        className="inline-flex items-center gap-1 text-body-sm font-medium text-fg-muted transition-colors hover:text-fg-primary"
      >
        <ArrowLeft className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        Все интеграции
      </Link>

      <header className="mt-6">
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          {integration.shortName} · интеграция
        </p>
        <h1 className="brikko-h1 mt-3">{integration.title}</h1>
        <p className="brikko-lede mt-4">{integration.intro}</p>
      </header>

      <WhySection integration={integration} />
      <SetupSection integration={integration} />
      <ModelsSection />
      <CostSection integration={integration} />
      <FailoverSection integration={integration} />
      <DocsSection />

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ за 5 минут — на баланс зачислим 200 ₽ welcome-бонуса.
          Карта не нужна.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="mt-12">
      <h2 id={id} className="brikko-h2">
        {title}
      </h2>
      <div className="mt-4 text-body text-fg-muted">{children}</div>
    </section>
  );
}

function WhySection({ integration }: { integration: Integration }) {
  return (
    <Section id="why-brikko" title="Зачем подключать через Brikko">
      <ul className="space-y-3">
        {integration.whyBullets.map((bullet) => (
          <li key={bullet} className="flex gap-3">
            <Check
              className="mt-0.5 h-5 w-5 shrink-0 text-fg-primary"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <span>{bullet}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function SetupSection({ integration }: { integration: Integration }) {
  return (
    <Section id="setup" title="Установка за 30 секунд">
      <ol className="space-y-6">
        {integration.setupSteps.map((step, idx) => (
          <li key={step.title} className="flex gap-4">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--hairline)] bg-[var(--bg-elevated)] font-mono text-body-sm font-semibold text-fg-primary">
              {idx + 1}
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-body-large font-semibold text-fg-primary">
                {step.title}
              </h3>
              <p className="mt-1 text-body text-fg-muted">{step.body}</p>
              {step.code ? (
                <div className="brikko-code-shell mt-3">
                  {step.codeLabel ? (
                    <div className="brikko-code-label">{step.codeLabel}</div>
                  ) : null}
                  <pre className="brikko-code-pre">
                    <code>{step.code}</code>
                  </pre>
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function ModelsSection() {
  return (
    <Section id="models" title="Какие модели работают">
      <p>
        Через один ключ доступны 38 моделей от 6 провайдеров: GPT-5.5 / 5.5 Pro /
        GPT-5 / o3 / o4-mini / o1* / GPT-4o* (OpenAI, 15 моделей),
        Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5 + legacy 3.5/3 (Anthropic, 6),
        Gemini 3.1 Pro / 3 Flash / 2.5 семейство / 1.5 семейство (Google, 7),
        DeepSeek V4 Pro / V4 Flash / R1 / V3.2 (DeepSeek, 5),
        YandexGPT 5.1 Pro / 5 Lite (Яндекс, 2),
        GigaChat 2 Max / Pro / Lite (Сбер, 3).
      </p>
      <p className="mt-3">
        Для каждой модели — публичная цена в рублях, открытый каталог. Полные
        лимиты контекста, capabilities и
        калькулятором — на странице{' '}
        <Link href="/models" className="brikko-link">
          /models
        </Link>
        .
      </p>
    </Section>
  );
}

function CostSection({ integration }: { integration: Integration }) {
  return (
    <Section id="cost" title="Стоимость в рублях">
      <p>
        Расход списывается из рублёвого баланса по реальному количеству токенов.
        Минимальный порог — 100 ₽ на пополнение через ЮKassa. Чек самозанятого
        приходит автоматически на каждое пополнение, оферта и акт — по тарифам
        Pro/Team/Business.
      </p>
      <ul className="mt-5 space-y-3">
        {integration.costExamples.map((example) => (
          <li key={example.scenario} className="brikko-card-flat">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="font-medium text-fg-primary">{example.scenario}</p>
              <p className="font-mono text-body-large font-semibold text-fg-primary">
                {formatRub(example.rub)}
              </p>
            </div>
            <p className="mt-1 text-body-sm text-fg-faint">{example.detail}</p>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-body-sm text-fg-faint">
        Примеры — расчётные, для типового размера запроса. Точную смету для своих
        сценариев можно собрать в{' '}
        <Link href="/pricing" className="brikko-link">
          калькуляторе на /pricing
        </Link>
        .
      </p>
    </Section>
  );
}

function FailoverSection({ integration }: { integration: Integration }) {
  return (
    <Section id="failover" title="Что если упадёт провайдер">
      <p>{integration.failoverNote}</p>
      <p className="mt-3">
        Подробное описание стратегии smart routing, лимитов и приоритетов
        резервирования —{' '}
        <Link href={'/docs/smart-routing' as Route} className="brikko-link">
          /docs/smart-routing
        </Link>
        .
      </p>
    </Section>
  );
}

function DocsSection() {
  return (
    <Section id="docs" title="Документация">
      <ul className="space-y-2">
        <li>
          <Link href="/docs" className="brikko-link">
            /docs
          </Link>{' '}
          — справочник API, SDK, аутентификация.
        </li>
        <li>
          <Link href={'/docs/cookbook' as Route} className="brikko-link">
            /docs/cookbook
          </Link>{' '}
          — готовые рецепты под бизнес-задачи.
        </li>
        <li>
          <Link href={'/docs/smart-routing' as Route} className="brikko-link">
            /docs/smart-routing
          </Link>{' '}
          — как работает failover между провайдерами.
        </li>
      </ul>
    </Section>
  );
}
