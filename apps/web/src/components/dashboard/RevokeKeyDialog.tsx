'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Field, FieldHelper } from '@/components/ui/form';
import { toast } from '@/components/ui/toast';
import { useRevokeKey } from '@/lib/auth';
import type { ApiKey } from '@/lib/types';

/**
 * Typing-confirmation паттерн (GitHub/Stripe). Кнопка disabled пока имя ключа не введено.
 * UX: типография «отзыв = удаление». Разово блокирует случайный клик, добавляет ~2 сек на действие.
 */
interface RevokeKeyDialogProps {
  apiKey: ApiKey;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RevokeKeyDialog({ apiKey, open, onOpenChange }: RevokeKeyDialogProps) {
  const [confirmation, setConfirmation] = useState('');
  const revoke = useRevokeKey();
  const matches = confirmation.trim() === apiKey.name;

  async function handleRevoke() {
    try {
      await revoke.mutateAsync(apiKey.id);
      toast.success(`Ключ ${apiKey.name} отозван. Запросы с него получат 401.`);
      onOpenChange(false);
      setConfirmation('');
    } catch {
      toast.error('Не удалось отозвать ключ. Попробуй ещё раз.');
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setConfirmation('');
        onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Отозвать ключ {apiKey.name}?</DialogTitle>
          <DialogDescription>
            Запросы с этим ключом сразу получат 401. Это действие нельзя отменить.
          </DialogDescription>
        </DialogHeader>

        <Field className="mt-4">
          <Label htmlFor="revoke-confirm">
            Чтобы подтвердить, введи имя ключа:{' '}
            <code className="rounded bg-gray-100 px-1 font-mono text-xs text-gray-900">
              {apiKey.name}
            </code>
          </Label>
          <Input
            id="revoke-confirm"
            value={confirmation}
            onChange={(e) => setConfirmation(e.target.value)}
            autoComplete="off"
            data-testid="revoke-confirm-input"
          />
          <FieldHelper id="revoke-confirm-help">Регистр важен.</FieldHelper>
        </Field>

        <DialogFooter>
          <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!matches}
            loading={revoke.isPending}
            onClick={handleRevoke}
            data-testid="revoke-confirm-submit"
          >
            Отозвать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
