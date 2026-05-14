'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
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
import { Field, FieldError } from '@/components/ui/form';
import { toast } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';
import { useInviteSeat } from '@/lib/auth';

const schema = z.object({
  email: z.string().min(1, 'Введи email').email('Проверь формат email'),
  role: z.enum(['admin', 'member']),
});
type FormValues = z.infer<typeof schema>;

interface InviteSeatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function InviteSeatDialog({ open, onOpenChange }: InviteSeatDialogProps) {
  const invite = useInviteSeat();
  const {
    register,
    handleSubmit,
    setError,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', role: 'member' },
  });

  async function onSubmit(values: FormValues) {
    try {
      const res = await invite.mutateAsync({
        email: values.email.trim().toLowerCase(),
        role: values.role,
      });
      toast.success(`Приглашение отправлено на ${res.email}`);
      onOpenChange(false);
      reset();
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'invite_already_member') {
        setError('email', { message: 'Этот email уже в команде' });
        return;
      }
      if (err instanceof ApiClientError && err.type === 'seat_limit_reached') {
        setError('email', { message: err.message });
        return;
      }
      toast.error('Не удалось отправить приглашение. Попробуй ещё раз.');
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form noValidate onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Пригласить в команду</DialogTitle>
            <DialogDescription>
              Приглашение действует 7 дней. Общий баланс, общие ключи, общая аналитика.
            </DialogDescription>
          </DialogHeader>

          <Field>
            <Label htmlFor="invite-email">Email</Label>
            <Input
              id="invite-email"
              type="email"
              autoComplete="email"
              autoFocus
              invalid={Boolean(errors.email)}
              aria-describedby="invite-email-error"
              {...register('email')}
            />
            <FieldError id="invite-email-error">{errors.email?.message}</FieldError>
          </Field>

          <Field>
            <Label htmlFor="invite-role">Роль</Label>
            <select
              id="invite-role"
              className="h-10 rounded-md border border-gray-300 bg-white px-3 text-body text-gray-900 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
              {...register('role')}
            >
              <option value="member">Member — пользоваться API и видеть аналитику</option>
              <option value="admin">Admin — также управлять ключами и приглашениями</option>
            </select>
          </Field>

          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button
              type="submit"
              loading={isSubmitting || invite.isPending}
              data-testid="invite-submit"
            >
              Отправить приглашение
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
