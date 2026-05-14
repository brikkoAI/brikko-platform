'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { TariffCard, TARIFF_CATALOG, type TariffCardData } from '@/components/dashboard/TariffCard';
import {
  UpgradeConfirmModal,
  TARIFF_PRICE_KOP,
} from '@/components/dashboard/UpgradeConfirmModal';
import { useAccount, useBalance } from '@/lib/auth';
import type { Kopecks, TariffChangeResponse, TariffSlug } from '@/lib/types';

/**
 * /app/billing/upgrade — выбор и переключение тарифа.
 *
 * UX-обоснование структуры:
 *  - Grid 1 col на мобиле, 2 col на md, 3 col на lg. 5 карточек: 3 в первой строке +
 *    2 во второй (на lg). Пустую ячейку оставляем пустой — wrap'ить в «и ещё»-CTA было бы
 *    плохим вкусом.
 *  - Pro Privacy визуально выделен (gradient-border + 152-ФЗ бейдж) — это маркетинговая
 *    середина, на которую мы хотим переводить максимум юзеров.
 *  - Business / Business+ — отдельная карточка с «Связаться с продажами» (mailto), потому
 *    что у них нет self-serve checkout.
 */

export default function UpgradeTariffPage() {
  const router = useRouter();
  const account = useAccount();
  const balance = useBalance();
  const [selected, setSelected] = useState<TariffCardData | null>(null);
  const [open, setOpen] = useState(false);

  const currentTariff = (account.data?.tariff ?? null) as TariffSlug | null;
  const balanceKopecks = (balance.data?.balance_kopecks ?? 0) as Kopecks;

  function handleSelect(slug: TariffSlug) {
    const data = TARIFF_CATALOG.find((t) => t.slug === slug);
    if (!data) return;
    if (data.contactSales) {
      // Business / Business+ — нет self-serve checkout. Открываем mailto.
      // На enterprise-уровне мы хотим живой контакт, а не «купи через форму».
      window.location.href =
        'mailto:sales@brikko.ai?subject=Brikko%20Business%20—%20вопрос%20о%20тарифе';
      return;
    }
    setSelected(data);
    setOpen(true);
  }

  function handleSuccess(response: TariffChangeResponse) {
    setOpen(false);
    setSelected(null);
    toast.success('Тариф изменён.');
    if (response.requires_pii_setup) {
      // Privacy-тарифы — отправляем в Settings → Privacy, чтобы пользователь сразу
      // включил PII-маскинг. Без этого подписка работает, но PDn идут в OpenAI без редакта —
      // это противоречит самому смыслу Privacy-тарифа.
      router.push('/app/settings?section=privacy&onboarding=pii');
    } else {
      router.push('/app/billing');
    }
  }

  if (account.isLoading || balance.isLoading) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <Skeleton className="h-12 w-1/2" />
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-80 w-full" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex flex-col gap-2">
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<ArrowLeft className="h-4 w-4" />}
          onClick={() => router.push('/app/billing')}
          className="self-start -ml-2"
        >
          К биллингу
        </Button>
        <h1 className="text-3xl font-semibold text-gray-900">Тарифы</h1>
        <p className="text-body text-gray-500">
          Выбери тариф под команду и юридическую модель работы. Переключение —
          мгновенное, списание — с текущего баланса.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {TARIFF_CATALOG.map((tariff) => (
          <TariffCard
            key={tariff.slug}
            data={tariff}
            currentTariff={currentTariff}
            onSelect={handleSelect}
          />
        ))}
      </div>

      <p className="text-body-sm text-gray-500">
        Тарифы Business и Business+ оформляются индивидуально через продажи. Свяжись —
        оформим за 1-2 дня вместе с договором.
      </p>

      <UpgradeConfirmModal
        open={open}
        onOpenChange={setOpen}
        tariff={selected}
        balanceKopecks={balanceKopecks}
        priceKopecks={selected ? TARIFF_PRICE_KOP[selected.slug] : 0}
        onSuccess={handleSuccess}
      />
    </div>
  );
}
