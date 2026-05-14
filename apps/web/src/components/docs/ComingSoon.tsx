/**
 * ComingSoon — единая заглушка для разделов в roadmap, которые мы пока
 * не написали детально.
 *
 * UX-обоснование:
 *   - Честность важнее белой страницы. Пользователь, кликнувший «n8n
 *     интеграция» в sidebar, не должен видеть 404 или generic «soon».
 *     Здесь — что именно появится, когда (если знаем), и куда обратиться,
 *     если нужно прямо сейчас.
 *   - support@brikko.ru как escape-hatch — solo-стартап-практика. Если
 *     у клиента горит, мы соберём руками то, что в roadmap.
 *   - Один компонент = одно место для смены copy/стиля для всех 10
 *     заглушек. DRY.
 *   - НЕ показываем countdown / progress bar — нечестно и стрессово.
 *     Просто сообщаем «в работе» + alternative.
 */

import Link from 'next/link';
import type { Route } from 'next';

interface Props {
  /** Заголовок страницы — например, «Anthropic Messages API» */
  title: string;
  /** Краткое описание что это / зачем нужно */
  description: string;
  /** Опционально — ETA «Q3 2026» / «после Sprint 14» */
  eta?: string;
  /** Опциональная alternative-секция — куда сходить пока ждёшь */
  alternative?: {
    label: string;
    href: Route;
  };
}

export function ComingSoon({ title, description, eta, alternative }: Props) {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация · в работе
        </p>
        <h1 className="brikko-h1 mt-3">{title}</h1>
        <p className="brikko-lede mt-4">{description}</p>
      </header>

      <section className="mt-8 brikko-card-flat">
        <p className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
          Статус раздела
        </p>
        <p className="brikko-prose mt-3">
          Этот раздел сейчас в разработке. {eta ? `Ориентировочно: ${eta}.` : ''}{' '}
          API-endpoint <strong className="text-fg-primary">уже работает</strong> —
          описание контракта здесь появится, как только мы пройдём финальное
          ревью с первыми клиентами. Если нужна спецификация прямо сейчас —
          напишите{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          , вышлем технический PDF в течение рабочего дня.
        </p>
      </section>

      {alternative ? (
        <section className="mt-8">
          <h2 className="brikko-h2">Что почитать пока</h2>
          <p className="brikko-prose mt-4">
            Связанный раздел, который уже готов:
          </p>
          <Link href={alternative.href} className="brikko-card-link mt-6">
            <h3 className="text-lg font-semibold text-fg-primary">
              {alternative.label}
            </h3>
            <p className="mt-2 text-body-sm text-fg-muted">
              Открыть документацию по этому разделу
            </p>
          </Link>
        </section>
      ) : null}

      <section className="mt-8">
        <h2 className="brikko-h2">Связь</h2>
        <p className="brikko-prose mt-4">
          Если вы хотите ускорить выпуск этого раздела или у вас есть конкретный
          use-case, который мы должны учесть — напишите{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          . Мы — solo-стартап и приоритизируем roadmap по реальным запросам
          клиентов.
        </p>
      </section>
    </article>
  );
}
