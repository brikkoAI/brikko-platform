'use client';

import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Field, FieldError, FieldHelper } from '@/components/ui/form';
import { useTwoFactorDisable } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

/**
 * Отключение 2FA — требует свежий TOTP-код.
 *
 * UX-обоснование:
 *  - Требуем code (а не «подтверждение пароля»), потому что: если злоумышленник украл
 *    cookie/session — он не может отключить 2FA и закрепить compromise. Код = «physical
 *    presence» доказательство, что у юзера есть телефон.
 *  - Maxlength 6, inputMode=numeric, autocomplete=one-time-code — UX-сахар для iOS
 *    (предлагает код из SMS / authenticator).
 */

interface TwoFactorDisableModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
}

export function TwoFactorDisableModal({ open, onOpenChange, onSuccess }: TwoFactorDisableModalProps) {
  const disable = useTwoFactorDisable();
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setCode('');
      setError(null);
    }
  }, [open]);

  async function handleDisable() {
    if (!/^\d{6}$/.test(code)) {
      setError('Введи 6-значный код');
      return;
    }
    setError(null);
    try {
      await disable.mutateAsync(code);
      toast.success('2FA отключена.');
      onSuccess();
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'invalid_credentials') {
        setError('Неверный код. Попробуй свежий.');
        return;
      }
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось отключить 2FA.';
      toast.error(message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" data-testid="2fa-disable-modal">
        <DialogHeader>
          <DialogTitle>Отключить 2FA?</DialogTitle>
          <DialogDescription>
            Введи код из приложения, чтобы подтвердить, что это ты. После отключения
            аккаунт будет защищён только паролем.
          </DialogDescription>
        </DialogHeader>

        <Field className="mt-4">
          <Label htmlFor="2fa-disable-code">6-значный код</Label>
          <Input
            id="2fa-disable-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            invalid={Boolean(error)}
            data-testid="2fa-disable-code-input"
          />
          <FieldHelper id="2fa-disable-help">Из Google Authenticator / Authy / 1Password.</FieldHelper>
          <FieldError id="2fa-disable-error">{error ?? undefined}</FieldError>
        </Field>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button
            variant="destructive"
            onClick={handleDisable}
            loading={disable.isPending}
            data-testid="2fa-disable-cta"
          >
            Отключить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
