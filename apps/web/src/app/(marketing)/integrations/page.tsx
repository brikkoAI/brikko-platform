import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { INTEGRATIONS } from '@/lib/integrations';

export const metadata: Metadata = {
  title: 'Интеграции с AI-coding-агентами · Brikko',
  description:
    'Подключи Cursor, Claude Code, Codex CLI, Copilot CLI или Gemini CLI к Brikko gateway. Рублёвая оплата, 6 провайдеров, failover.',
  alternates: { canonical: '/integrations' },
};

/**
 * Index-страница раздела «Интеграции» (Cream Studio v6).
 *
 * UX: server-component, SSG, никаких клиентских интерактивов — карточка =
 * единая ссылка с focus-ring + hover. Логотипов IDE в public/ нет, используем
 * lucide-иконки в hairline-rounded плашке — узнаваемо и нейтрально.
 */
export default function IntegrationsIndexPage() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16 lg:py-24">
      <header className="max-w-3xl">
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Интеграции
        </p>
        <h1 className="brikko-h1 mt-3">Подключи Brikko к своему AI-агенту</h1>
        <p className="brikko-lede mt-4">
          Brikko — OpenAI-совместимый gateway к 38 моделям от 6 провайдеров.
          Меняешь base_url в IDE или CLI — и тот же агент работает на рублёвом
          балансе с автоматическим failover.
        </p>
      </header>

      <ul className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
        {INTEGRATIONS.map((integration) => {
          const Icon = integration.icon;
          return (
            <li key={integration.slug} className="h-full">
              <Link
                href={`/integrations/${integration.slug}` as Route}
                className="brikko-card-link group h-full"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-[var(--hairline)] bg-[var(--bg-elevated)] text-fg-primary">
                  <Icon
                    className="h-6 w-6"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                </div>

                <h2 className="mt-5 text-lg font-semibold text-fg-primary">
                  {integration.shortName}
                </h2>
                <p className="mt-1 text-body-sm text-fg-faint">
                  {integration.cardSubtitle}
                </p>

                <p className="mt-4 flex-1 text-body-sm text-fg-muted">
                  {integration.intro.split('.')[0]}.
                </p>

                <span className="mt-5 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                  Узнать больше
                  <ArrowRight
                    className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      <p className="mt-10 max-w-3xl text-body-sm text-fg-muted">
        Не нашёл свой инструмент? Brikko совместим с любым клиентом, поддерживающим
        OpenAI Chat Completions API — Continue.dev, aider, plandex,
        open-interpreter, LangChain, Vercel AI SDK. Достаточно перенаправить
        base_url на{' '}
        <code className="brikko-code-inline">https://api.brikko.ru/v1</code>.
      </p>
    </section>
  );
}
