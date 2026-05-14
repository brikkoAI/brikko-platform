import Link from 'next/link';
import type { Route } from 'next';
import { ArrowRight } from 'lucide-react';
import { INTEGRATIONS } from '@/lib/integrations';

/**
 * Секция «Работает с твоим IDE» на лендинге, между Features и SmartRouterDemo.
 * UX-логика расположения: после того как Features рассказал про smart routing
 * + 6 провайдеров, гость спрашивает «а как это применить в моих инструментах?».
 * Эта секция — конкретный ответ: 5 узнаваемых имён IDE/CLI с прямыми ссылками
 * на страницы интеграций. Distribution-play.
 */
export function Integrations() {
  return (
    <section className="bg-white py-16 lg:py-24">
      <div className="mx-auto max-w-6xl px-6">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-semibold tracking-tight text-gray-900">
            Работает с твоим IDE
          </h2>
          <p className="mt-3 text-body text-gray-700">
            Brikko — OpenAI-совместимый endpoint. Меняешь base_url — и тот же AI-агент работает
            на рублёвом балансе с failover. Никакой магии и никаких форков IDE.
          </p>
        </div>

        <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {INTEGRATIONS.map((integration) => {
            const Icon = integration.icon;
            return (
              <li key={integration.slug}>
                <Link
                  href={`/integrations/${integration.slug}` as Route}
                  className="group flex h-full flex-col items-start rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition-colors hover:border-brand-300 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-2"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-50 text-brand-600 group-hover:bg-white">
                    <Icon className="h-5 w-5" strokeWidth={1.5} aria-hidden="true" />
                  </div>
                  <p className="mt-4 font-semibold text-gray-900 group-hover:text-brand-700">
                    {integration.shortName}
                  </p>
                  <p className="mt-1 text-body-sm text-gray-500">
                    {integration.cardSubtitle}
                  </p>
                </Link>
              </li>
            );
          })}
        </ul>

        <div className="mt-8">
          <Link
            href={'/integrations' as Route}
            className="inline-flex items-center gap-1 text-body font-medium text-brand-700 hover:underline"
          >
            Все интеграции
            <ArrowRight className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </Link>
        </div>
      </div>
    </section>
  );
}
