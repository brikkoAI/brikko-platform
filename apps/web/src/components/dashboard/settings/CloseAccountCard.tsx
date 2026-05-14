'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Field, FieldError, FieldHelper } from '@/components/ui/form';
import { Banner } from '@/components/ui/banner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useAccount, useCancelClosure, useCloseAccount } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';
import { formatDate } from '@/lib/utils';

/**
 * Закрытие аккаунта — destructive flow.
 *
 * UX-обоснование:
 *  - Двойное подтверждение: (1) клик на «Закрыть», (2) modal с warning + ввод магической
 *    фразы «BRIKKO CLOSE». Это защита от случайного клика. Аналог: GitHub repo deletion,
 *    Stripe account closure. Цена ошибки слишком высокая.
 *  - Reason — optional. Не enforce: формальная причина не нужна нам, нужны качественные
 *    open-text feedback'и от тех, кто сам захочет.
 *  - 30 дней grace-period — пишем в warning явно. После 30 дней — soft-delete; через 1 год
 *    hard-delete (152-ФЗ совместимо).
 *  - Кнопка «Закрыть» в самом низу карточки + красная — это ride-the-line: пользователь
 *    должен дойти до неё намеренно, scroll до низу. Не топовая кнопка.
 */

const CONFIRM_PHRASE = 'BRIKKO CLOSE';

interface CloseAccountCardProps {
  /** Если true — кнопка закрытия скрывается (closure уже scheduled). */
  closureScheduled: boolean;
}

export function CloseAccountCard({ closureScheduled }: CloseAccountCardProps) {
  const [open, setOpen] = useState(false);
  const account = useAccount();
  const cancelClosure = useCancelClosure();

  if (closureScheduled) {
    // На /settings/security банер скрыт (см. ClosureBanner) — поэтому здесь
    // нужен полноценный scheduled-state с явным Cancel-CTA. Иначе пользователь,
    // зашедший прямо на этот URL, не получит способа отменить закрытие.
    const accountData = account?.data;
    const closureAt =
      accountData?.closure_scheduled_at ?? accountData?.scheduled_closure_at ?? null;
    return (
      <Card className="border-warning-200" data-testid="close-account-card-scheduled">
        <CardTitle className="text-warning-600">Закрытие уже запланировано</CardTitle>
        <CardDescription className="mt-1">
          {closureAt
            ? `Аккаунт будет закрыт ${formatDate(closureAt)}. До этой даты API-ключи продолжают работать.`
            : 'Закрытие запланировано. До этой даты API-ключи продолжают работать.'}
        </CardDescription>
        <Button
          variant="secondary"
          className="mt-4"
          loading={cancelClosure.isPending}
          onClick={async () => {
            try {
              await cancelClosure.mutateAsync();
              toast.success('Закрытие аккаунта отменено.');
            } catch {
              toast.error('Не удалось отменить. Попробуй ещё раз.');
            }
          }}
          data-testid="close-account-cancel-button"
        >
          Отменить закрытие
        </Button>
      </Card>
    );
  }

  return (
    <>
      <Card className="border-error-200/60">
        <CardTitle className="text-error-600">Закрыть аккаунт</CardTitle>
        <CardDescription className="mt-1">
          Запланируем удаление через 30 дней. До этой даты можно отменить — данные
          сохранятся. Через 1 год после закрытия данные удаляются безвозвратно.
        </CardDescription>

        <ul className="mt-4 flex flex-col gap-1.5 text-body-sm text-gray-700">
          <li className="flex items-start gap-2">
            <span className="text-gray-400">•</span>
            API-ключи продолжают работать в течение 30 дней.
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-400">•</span>
            Команда теряет доступ сразу — owner закрывает за всех.
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-400">•</span>
            Закрытие можно отменить в любой момент в этих настройках.
          </li>
        </ul>

        <Button
          variant="destructive"
          className="mt-5"
          onClick={() => setOpen(true)}
          data-testid="close-account-button"
        >
          Закрыть аккаунт
        </Button>
      </Card>

      <CloseAccountDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

function CloseAccountDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const close = useCloseAccount();
  const [phrase, setPhrase] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPhrase('');
      setReason('');
      setError(null);
    }
  }, [open]);

  async function handleClose() {
    if (phrase.trim() !== CONFIRM_PHRASE) {
      setError(`Введи «${CONFIRM_PHRASE}» точно как написано — это защита от случайного клика.`);
      return;
    }
    setError(null);
    try {
      await close.mutateAsync(reason.trim() || undefined);
      toast.success('Аккаунт будет закрыт через 30 дней. Можно отменить в Настройках.');
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось запланировать закрытие.';
      toast.error(message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="close-account-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-error-600">
            <AlertTriangle className="h-5 w-5" strokeWidth={2} aria-hidden="true" />
            Закрыть аккаунт?
          </DialogTitle>
          <DialogDescription>
            Запланируем удаление через 30 дней. До этой даты ты можешь отменить — всё вернётся.
          </DialogDescription>
        </DialogHeader>

        <Banner
          variant="warning"
          title="Что произойдёт"
          description={
            <ul className="mt-1 flex flex-col gap-0.5 text-body-sm">
              <li>• Через 30 дней — soft-delete: ключи отозваны, доступ закрыт.</li>
              <li>• Через 1 год после закрытия — hard-delete: данные удаляются безвозвратно.</li>
              <li>• До закрытия — API-ключи и подписка продолжают работать.</li>
            </ul>
          }
          className="mt-4"
        />

        <Field className="mt-4">
          <Label htmlFor="close-reason">Причина закрытия (опционально)</Label>
          <Input
            id="close-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
            placeholder="Что не подошло? Это поможет нам стать лучше."
            data-testid="close-reason-input"
          />
        </Field>

        <Field className="mt-4">
          <Label htmlFor="close-phrase">
            Введи «{CONFIRM_PHRASE}», чтобы подтвердить
          </Label>
          <Input
            id="close-phrase"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            invalid={Boolean(error)}
            autoComplete="off"
            spellCheck={false}
            aria-describedby="close-phrase-error close-phrase-help"
            data-testid="close-phrase-input"
          />
          <FieldHelper id="close-phrase-help">
            Регистрозависимо. Защита от случайного клика.
          </FieldHelper>
          <FieldError id="close-phrase-error">{error ?? undefined}</FieldError>
        </Field>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button
            variant="destructive"
            onClick={handleClose}
            loading={close.isPending}
            data-testid="close-account-confirm-cta"
          >
            Закрыть аккаунт через 30 дней
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
