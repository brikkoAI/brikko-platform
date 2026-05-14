'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { Copy, Check } from 'lucide-react';
import { useEffect, useState } from 'react';
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
import { Banner } from '@/components/ui/banner';
import { HelperTooltip } from '@/components/ui/helper-tooltip';
import { toast } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';
import { useCreateKey } from '@/lib/auth';
import { track } from '@/lib/analytics';
import type { ApiKeyCreated } from '@/lib/types';

/**
 * Modal вместо Drawer — выбор сделан намеренно:
 * - Контекст создания короткий (имя + scope), пользователю не нужен «back-context» страницы.
 * - После создания мы блокируем юзера в modal'е, пока он не скопирует ключ — иначе он его потеряет.
 */

const schema = z.object({
  name: z.string().trim().min(1, 'Имя не может быть пустым').max(64, 'Максимум 64 символа'),
  scope: z.enum(['full', 'read_only']),
});

type FormValues = z.infer<typeof schema>;

interface CreateKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Sprint 12 §1 (PRD 29 §2). Когда задан — после успешного создания и закрытия
   * диалога вызываем callback с full_key. Родитель показывает QuickStart с
   * pre-filled ключом — это «автоматически поверх Quick Start» из PRD.
   *
   * Контр-аргумент: можно было хранить full_key в react-query cache, но это
   * ломает security-инвариант «секрет не персистится после закрытия диалога».
   * Callback-pattern передаёт ключ ровно одному получателю на одну сессию.
   */
  onCreated?: (created: ApiKeyCreated) => void;
}

export function CreateKeyDialog({ open, onOpenChange, onCreated }: CreateKeyDialogProps) {
  const create = useCreateKey();
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const {
    register,
    handleSubmit,
    setError,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', scope: 'full' },
  });

  useEffect(() => {
    // При закрытии — сбросим состояние, иначе следующий open покажет старый ключ.
    if (!open) {
      setCreatedKey(null);
      setCopied(false);
      reset();
    }
  }, [open, reset]);

  async function onSubmit(values: FormValues) {
    try {
      const created = await create.mutateAsync(values);
      setCreatedKey(created);
      track('key_created', { scope: created.scope });
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'key_limit_reached') {
        setError('name', { message: err.message });
        return;
      }
      const msg =
        err instanceof ApiClientError ? err.message : 'Не удалось создать ключ. Попробуй ещё раз.';
      toast.error(msg);
    }
  }

  async function copyKey() {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.full_key);
      setCopied(true);
      toast.success('Скопировано');
    } catch {
      toast.error('Не удалось скопировать. Скопируй вручную.');
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        onInteractOutside={(e) => {
          // Click-outside по-прежнему блокируем при `!copied` — это accident-prevention
          // (случайный клик за пределами модалки). Кнопка «Готово» — явный intent,
          // её мы НЕ блокируем (см. handleDone). Баг 2026-05-02 #4.
          if (createdKey && !copied) e.preventDefault();
        }}
      >
        {createdKey ? (
          <>
            <DialogHeader>
              <DialogTitle>Ключ создан</DialogTitle>
              <DialogDescription>
                Скопируй сейчас — больше мы его не покажем.
              </DialogDescription>
            </DialogHeader>

            <div className="mt-4 flex flex-col gap-3">
              <div className="rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-body-sm break-all text-gray-900">
                {createdKey.full_key}
              </div>
              <Button
                type="button"
                variant="primary"
                onClick={copyKey}
                leftIcon={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                data-testid="copy-full-key"
              >
                {copied ? 'Скопировано' : 'Скопировать ключ'}
              </Button>
              <Banner
                variant="warning"
                title="Это последний раз, когда ты видишь полный ключ"
                description="Если потеряешь — придётся создавать заново. В UI остаётся только префикс."
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  // Передаём ключ родителю до закрытия — родитель скроллит и
                  // pre-fill'ит QuickStart. Само закрытие чистит state в useEffect.
                  if (createdKey) onCreated?.(createdKey);
                  onOpenChange(false);
                }}
                data-testid="create-key-done"
              >
                Готово
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form noValidate onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
            <DialogHeader>
              <DialogTitle>Новый API-ключ</DialogTitle>
              <DialogDescription>
                Имя видно только тебе. Используй его, чтобы понимать, что куда подключено.
              </DialogDescription>
            </DialogHeader>

            <Field>
              <div className="flex items-center gap-1.5">
                <Label htmlFor="key-name">Имя ключа</Label>
                <HelperTooltip content="Помогает отличать ключи между собой. Видно только в дашборде, в API не передаётся." />
              </div>
              <Input
                id="key-name"
                placeholder="Production"
                autoFocus
                invalid={Boolean(errors.name)}
                aria-describedby="key-name-help key-name-error"
                {...register('name')}
              />
              <p id="key-name-help" className="text-body-sm text-gray-500">
                Помогает отличать ключи между собой. Видно только в дашборде, в API не передаётся.
              </p>
              <FieldError id="key-name-error">{errors.name?.message}</FieldError>
            </Field>

            <Field>
              <div className="flex items-center gap-1.5">
                <Label htmlFor="key-scope">Права</Label>
                <HelperTooltip content="Для интеграций в проде использовать full. Для аудитов и dashboards — read-only." />
              </div>
              <select
                id="key-scope"
                className="h-10 rounded-md border border-gray-300 bg-white px-3 text-body text-gray-900 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
                aria-describedby="key-scope-help"
                {...register('scope')}
              >
                <option value="full">Полный доступ</option>
                <option value="read_only">Только чтение (usage / billing)</option>
              </select>
              <p id="key-scope-help" className="text-body-sm text-gray-500">
                Read-only — только чтение каталога моделей и текущего баланса. Full — все API-эндпоинты.
              </p>
            </Field>

            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
                Отмена
              </Button>
              <Button
                type="submit"
                loading={isSubmitting || create.isPending}
                data-testid="create-key-submit"
              >
                Создать ключ
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
