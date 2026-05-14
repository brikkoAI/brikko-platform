'use client';

import { Check, Sparkles, ShieldCheck, Phone } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { TariffSlug } from '@/lib/types';

/**
 * Карточка тарифа на странице /app/billing/upgrade.
 *
 * UX-обоснование:
 *  - Pro Privacy и Custom — emphasis-вариант с gradient-border. Это лучше
 *    «бейджика-наклейки» сверху, потому что тарифы должны быть **сравнимы** друг с
 *    другом по высоте и выравниванию: один общий каркас + только border'а отличается.
 *  - «Текущий тариф» — отдельное состояние карточки (border-success + Badge). НЕ
 *    серый «disabled», потому что юзер должен видеть что у него **активно**, не «что
 *    нельзя нажать». Disable вешаем только на CTA.
 *  - 5-7 фич — это потолок ant-marketing-style (Stripe, Linear, Vercel — все в этом
 *    диапазоне). Больше = wall of text, меньше = «чем платный отличается от free?».
 */

export type TariffEmphasis = 'none' | 'privacy' | 'enterprise';

export interface TariffCardData {
  /** Slug тарифа — отправляется в backend без mapping'а. */
  slug: TariffSlug;
  /** Название для UI: «Pro Features», «Pro Privacy». */
  name: string;
  /** Цена в виде строки — «1 990 ₽/мес», «0 ₽/мес», «от 19 990 ₽/мес». */
  priceLabel: string;
  /** Подзаголовок для аудитории: «для одиночек», «команды 2-5». */
  audience: string;
  /** Список фич (5-7 строк). */
  features: string[];
  /** Visual emphasis. */
  emphasis: TariffEmphasis;
  /**
   * Если true — карточка показывает «Связаться с продажами» вместо upgrade CTA
   * (Custom → нет self-serve checkout, нужен менеджер).
   */
  contactSales?: boolean;
}

interface TariffCardProps {
  data: TariffCardData;
  /** Текущий тариф юзера. Для определения «активный / можно перейти». */
  currentTariff: TariffSlug | null | undefined;
  /** Pending state — отключает CTA пока запрос летит. */
  pending?: boolean;
  /** Click на CTA — родитель открывает confirm modal или редиректит на mailto. */
  onSelect: (slug: TariffSlug) => void;
}

export function TariffCard({ data, currentTariff, pending, onSelect }: TariffCardProps) {
  const isCurrent = currentTariff === data.slug || isLegacyMatch(currentTariff, data.slug);

  const wrapperClasses = cn(
    'relative flex h-full flex-col rounded-lg border bg-white p-6 transition-shadow',
    isCurrent
      ? 'border-success-200 ring-1 ring-success-200/50 shadow-sm'
      : data.emphasis === 'privacy'
        ? 'border-brand-600 ring-1 ring-brand-600/20 shadow-md'
        : data.emphasis === 'enterprise'
          ? 'border-gray-300 shadow-md'
          : 'border-gray-200 shadow-sm hover:shadow-md',
  );

  const ctaLabel = isCurrent
    ? 'Текущий тариф'
    : data.contactSales
      ? 'Связаться с продажами'
      : 'Перейти на этот тариф';

  return (
    <article
      className={wrapperClasses}
      data-testid={`tariff-card-${data.slug}`}
      data-tariff={data.slug}
      aria-current={isCurrent ? 'true' : undefined}
    >
      {data.emphasis === 'privacy' ? (
        <Badge variant="brand" className="absolute -top-2.5 left-6 bg-brand-600 text-white">
          <ShieldCheck className="h-3 w-3" aria-hidden="true" />
          152-ФЗ
        </Badge>
      ) : data.emphasis === 'enterprise' ? (
        <Badge variant="neutral" className="absolute -top-2.5 left-6">
          <Sparkles className="h-3 w-3" aria-hidden="true" />
          Custom
        </Badge>
      ) : null}

      {isCurrent ? (
        <Badge variant="success" className="absolute -top-2.5 right-6">
          Активный
        </Badge>
      ) : null}

      <header className="flex flex-col gap-1">
        <h3 className="text-lg font-semibold text-gray-900">{data.name}</h3>
        <p className="text-body-sm text-gray-500">{data.audience}</p>
      </header>

      <div className="mt-4 text-2xl font-semibold tabular-nums text-gray-900">
        {data.priceLabel}
      </div>

      <ul className="mt-5 flex flex-1 flex-col gap-2">
        {data.features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-body-sm text-gray-700">
            <Check
              className="mt-0.5 h-4 w-4 shrink-0 text-success-600"
              strokeWidth={2}
              aria-hidden="true"
            />
            <span>{f}</span>
          </li>
        ))}
      </ul>

      <div className="mt-6">
        <Button
          variant={isCurrent ? 'secondary' : data.emphasis === 'privacy' ? 'primary' : 'secondary'}
          className="w-full"
          disabled={isCurrent || pending}
          loading={pending}
          leftIcon={data.contactSales && !isCurrent ? <Phone className="h-4 w-4" /> : undefined}
          onClick={() => onSelect(data.slug)}
          data-testid={`tariff-cta-${data.slug}`}
          aria-label={`${ctaLabel}: ${data.name}`}
        >
          {ctaLabel}
        </Button>
      </div>
    </article>
  );
}

/**
 * Backend `Tariff` использует legacy 'pro' для pro_features. Маппим обратно при
 * сравнении с TariffSlug, иначе на текущем PAYG юзере карточка Pro Features никогда
 * не получит «Текущий».
 */
function isLegacyMatch(current: TariffSlug | null | undefined, slug: TariffSlug): boolean {
  if (slug === 'pro_features' && (current as string) === 'pro') return true;
  return false;
}

// ============================================================
// Каталог тарифов — единый источник правды для UI.
// ============================================================
//
// Цены и фичи синхронизированы с BRIEF.md §7 + 02_Product/v1.5/16_ceo_decisions_2026-04-30.md.
// При изменении — синхронно обновлять PRICES_KOP в handlers.ts (PATCH /account/tariff).

export const TARIFF_CATALOG: TariffCardData[] = [
  {
    slug: 'payg',
    name: 'Pay-as-you-go',
    audience: 'Для разработчиков-одиночек',
    priceLabel: '0 ₽/мес',
    emphasis: 'none',
    features: [
      'Welcome-бонус 200 ₽ (~1 000 запросов GPT-5 mini)',
      'Все 38 моделей (OpenAI / Anthropic / Google / DeepSeek / Yandex / Sber)',
      'Smart-router с failover',
      'Чек самозанятого по каждой операции',
      'Биллинг по факту — без подписки',
      'Лимит: 1 API-ключ, 1 место в команде',
    ],
  },
  {
    slug: 'pro_features',
    name: 'Pro Features',
    audience: 'Команды 2-5 человек',
    priceLabel: '1 990 ₽/мес',
    emphasis: 'none',
    features: [
      'Всё из PAYG',
      'До 5 командных мест',
      'До 5 активных API-ключей',
      'Расширенная аналитика по моделям и ключам',
      'Чек НПД на каждое пополнение',
      'Telegram-уведомления о балансе и failover',
    ],
  },
  {
    slug: 'pro_privacy',
    name: 'Pro Privacy',
    audience: 'Команды + ПДн граждан РФ',
    priceLabel: '2 790 ₽/мес',
    emphasis: 'privacy',
    features: [
      'Всё из Pro Features',
      'PII-маскинг: ФИО, email, телефон, паспорт, ИНН, СНИЛС, карты',
      'PII-маскинг закрывает требования 152-ФЗ при работе с зарубежными LLM',
      'Договор на обработку ПДн с указанием суб-обработчиков',
      'Логирование с PII-redaction',
      'Снижение риска штрафа за нарушение 152-ФЗ',
    ],
  },
  {
    slug: 'team',
    name: 'Team',
    audience: 'Стартапы и агентства',
    priceLabel: 'от 5 990 ₽/мес',
    emphasis: 'none',
    features: [
      'Всё из Pro Features',
      'До 15 командных мест',
      'До 20 активных API-ключей',
      'Несколько проектов в одной админке',
      'Чек НПД на каждое пополнение',
      'Скидка 5% при потреблении >300k ₽/мес',
    ],
  },
  {
    // 2026-05-12: Business / Business+ переименованы в Custom. Brikko в формате
    // самозанятого (НПД) не выдаёт договор / акт / УПД / ЭДО / счёт-фактуру —
    // соответствующие фичи убраны. CTA contactSales, фичи общие, без
    // невыполнимых обещаний. Slug сохранён 'business' для backend-маршрутизации.
    slug: 'business',
    name: 'Custom',
    audience: 'Крупные клиенты, индивидуальные условия',
    priceLabel: 'По запросу',
    emphasis: 'enterprise',
    contactSales: true,
    features: [
      'Индивидуальные лимиты RPM и объёмов',
      'Выделенный канал поддержки',
      'PII-маскинг и защита 152-ФЗ',
      'Формат документов — обсуждаем при контакте',
      'support@brikko.ru — ответим в течение 1 рабочего дня',
    ],
  },
];
