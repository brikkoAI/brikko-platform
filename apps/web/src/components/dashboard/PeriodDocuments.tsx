'use client';

import { useState } from 'react';
import { FileDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { billingApi } from '@/lib/api';

/**
 * Period-level Comply Pack documents (Sprint 4 / Поток M).
 *
 * UX-обоснование:
 *   - Две независимые формы (Месячный счёт + Сводный отчёт) — потому что у них РАЗНЫЕ inputs:
 *     месяц (YYYY-MM) для УПД, диапазон (from..to) для свободного отчёта. Один комбо-form
 *     запутал бы.
 *   - Default'ы — current-month / последние 30 дней. 80% случаев бухгалтер заходит за
 *     прошлым/текущим месяцем; preset экономит клики.
 *   - Не делаем мутирующий запрос: просто открываем PDF в новой вкладке. Файл стримится
 *     с backend'а; никакой дополнительной типизации не надо.
 */
export function PeriodDocuments() {
  const today = new Date();
  const isoToday = today.toISOString().slice(0, 10);
  const isoMonth = today.toISOString().slice(0, 7);
  const thirtyDaysAgo = new Date(today.getTime() - 30 * 86_400_000)
    .toISOString()
    .slice(0, 10);

  const [period, setPeriod] = useState(isoMonth);
  const [from, setFrom] = useState(thirtyDaysAgo);
  const [to, setTo] = useState(isoToday);

  return (
    <Card>
      <CardTitle>Закрывающие документы</CardTitle>
      <CardDescription className="mt-1">
        Скачай счёт-фактуру за месяц или сводный отчёт по списаниям. Документы для бухгалтерии
        и сверки с провайдером.
      </CardDescription>

      <div className="mt-5 grid gap-6 md:grid-cols-2">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            window.open(billingApi.invoiceUrl(period), '_blank', 'noopener,noreferrer');
          }}
        >
          <Label htmlFor="period-month">Счёт-фактура за месяц</Label>
          <Input
            id="period-month"
            type="month"
            value={period}
            max={isoMonth}
            onChange={(e) => setPeriod(e.target.value)}
            required
          />
          <Button
            type="submit"
            variant="secondary"
            leftIcon={<FileDown className="h-4 w-4" />}
            data-testid="download-invoice"
          >
            Скачать счёт за месяц
          </Button>
        </form>

        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!from || !to) return;
            if (from > to) return;
            window.open(billingApi.summaryUrl({ from, to }), '_blank', 'noopener,noreferrer');
          }}
        >
          <Label htmlFor="period-from">Сводный отчёт за период</Label>
          <div className="flex items-center gap-2">
            <Input
              id="period-from"
              type="date"
              value={from}
              max={to}
              onChange={(e) => setFrom(e.target.value)}
              required
              aria-label="С какого числа"
            />
            <span aria-hidden="true" className="text-gray-400">
              —
            </span>
            <Input
              id="period-to"
              type="date"
              value={to}
              min={from}
              max={isoToday}
              onChange={(e) => setTo(e.target.value)}
              required
              aria-label="По какое число"
            />
          </div>
          <Button
            type="submit"
            variant="secondary"
            leftIcon={<FileDown className="h-4 w-4" />}
            data-testid="download-summary"
          >
            Скачать сводный отчёт
          </Button>
        </form>
      </div>
    </Card>
  );
}
