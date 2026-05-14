'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const STORAGE_KEY = 'brikko.welcome_dismissed';

/**
 * §3.1 Welcome modal — после первого signup'а. Показывается ровно один раз.
 *
 * UX-обоснование:
 *   - localStorage flag (не cookie, не account.welcome_dismissed на бэке): не нужен round-trip
 *     к API — modal'у нужно решение «показывать ли» до того, как загрузился /v1/account.
 *     Trade-off: смена браузера → modal покажется заново. Это OK — для PAYG-юзера полезный
 *     refresher; для Pro+ — не критично.
 *   - Один CTA «Начать» закрывает модалку и скроллит к Get Started checklist; «Пропустить»
 *     просто закрывает. Оба ставят флаг — повторно не покажем.
 */
export function WelcomeModal({ onStart }: { onStart?: () => void }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const dismissed = window.localStorage.getItem(STORAGE_KEY) === 'true';
      if (!dismissed) setOpen(true);
    } catch {
      // localStorage недоступен (privacy mode) — fallback: показать modal
      // (overhead не страшен; раздражения нет — закроется одним кликом).
      setOpen(true);
    }
  }, []);

  function dismiss() {
    try {
      window.localStorage.setItem(STORAGE_KEY, 'true');
    } catch {
      // No-op в privacy mode.
    }
    setOpen(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) dismiss();
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Добро пожаловать в Brikko</DialogTitle>
          <DialogDescription>
            Три шага до первого запроса. Займёт 5 минут.
          </DialogDescription>
        </DialogHeader>

        <ol className="my-2 flex flex-col gap-3 text-body text-gray-700">
          <li className="flex gap-3">
            <span
              aria-hidden="true"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-body-sm font-medium text-brand-700"
            >
              1
            </span>
            <div>
              <p className="font-medium text-gray-900">Создать API-ключ</p>
              <p className="text-body-sm text-gray-500">Один ключ для всех 38 моделей.</p>
            </div>
          </li>
          <li className="flex gap-3">
            <span
              aria-hidden="true"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-body-sm font-medium text-brand-700"
            >
              2
            </span>
            <div>
              <p className="font-medium text-gray-900">Сделать первый запрос</p>
              <p className="text-body-sm text-gray-500">Покажем готовый curl-сниппет.</p>
            </div>
          </li>
          <li className="flex gap-3">
            <span
              aria-hidden="true"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-body-sm font-medium text-brand-700"
            >
              3
            </span>
            <div>
              <p className="font-medium text-gray-900">Пополнить баланс</p>
              <p className="text-body-sm text-gray-500">
                На балансе уже <span className="font-semibold text-gray-900">200 ₽ welcome-бонуса</span> —
                хватит на ~1 000 запросов к GPT-5.4 mini, без карты.
              </p>
            </div>
          </li>
        </ol>

        <DialogFooter>
          <Button
            variant="secondary"
            onClick={dismiss}
            data-testid="welcome-modal-skip"
          >
            Пропустить
          </Button>
          <Button
            onClick={() => {
              dismiss();
              onStart?.();
            }}
            data-testid="welcome-modal-start"
          >
            Начать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
