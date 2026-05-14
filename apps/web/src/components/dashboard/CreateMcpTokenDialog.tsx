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
import { toast } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';
import { useCreateMcpToken } from '@/lib/auth';
import type { McpTokenCreated, McpTokenScope } from '@/lib/types';

/**
 * Modal для создания MCP-токена.
 *
 * Скопирован шаблон с CreateKeyDialog — два диалога настолько похожи, что
 * абстрагировать общую часть стоит сделать в S5 (когда добавим UsageMcp-tab
 * со своими uniformity-improvements). Сейчас — copy/paste/adapt чтобы не
 * блокироваться на общем компоненте.
 */

// S3 (CEO 2026-05-12): расширили enum — 4 новых read-only scope + ``all``
// wildcard. ``all`` — дефолт нового токена для one-prompt onboarding из
// helper-skill (см. github.com/brikkoAI/brikko-helper). Restrictive
// scope-токены (специфичный scope) остаются для агентских tenant-ов.
// UI рендерит 4 наиболее частых выбора; "all" — рекомендуемый дефолт.
const schema = z.object({
  name: z.string().trim().min(1, 'Имя не может быть пустым').max(64, 'Максимум 64 символа'),
  scope: z.enum([
    'all',
    'read_account',
    'read_usage',
    'recommend_model',
    'list_models',
    'read_traces',
    'list_cookbook',
    'list_integrations',
  ]),
});

type FormValues = z.infer<typeof schema>;

interface CreateMcpTokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const SCOPE_HELP: Record<McpTokenScope, string> = {
  all: 'Полный доступ ко всем 7 read-only tools. Рекомендуемый дефолт для Claude/Cursor/Codex.',
  read_account: 'Только баланс, тариф, статус аккаунта.',
  read_usage: 'Только статистика расходов и токенов.',
  recommend_model: 'Только подбор оптимальной модели под задачу через router Brikko.',
  list_models: 'Только каталог моделей с рублёвыми ценами.',
  read_traces: 'Только последние 20 traces из BrikkoLens.',
  list_cookbook: 'Только 5 готовых рецептов из cookbook.',
  list_integrations: 'Только список tool-guides (Cursor / Cline / Claude Code и пр.).',
};

export function CreateMcpTokenDialog({ open, onOpenChange }: CreateMcpTokenDialogProps) {
  const create = useCreateMcpToken();
  const [createdToken, setCreatedToken] = useState<McpTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const {
    register,
    handleSubmit,
    setError,
    reset,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', scope: 'all' },
  });

  const selectedScope = watch('scope');

  useEffect(() => {
    if (!open) {
      setCreatedToken(null);
      setCopied(false);
      reset();
    }
  }, [open, reset]);

  async function onSubmit(values: FormValues) {
    try {
      const created = await create.mutateAsync(values);
      setCreatedToken(created);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'mcp_token_limit_reached') {
        setError('name', { message: err.message });
        return;
      }
      const msg =
        err instanceof ApiClientError
          ? err.message
          : 'Не удалось создать токен. Попробуй ещё раз.';
      toast.error(msg);
    }
  }

  async function copyToken() {
    if (!createdToken) return;
    try {
      await navigator.clipboard.writeText(createdToken.full_token);
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
          // Click-outside блокируем при `!copied` — accident-prevention.
          if (createdToken && !copied) e.preventDefault();
        }}
      >
        {createdToken ? (
          <>
            <DialogHeader>
              <DialogTitle>Токен создан</DialogTitle>
              <DialogDescription>
                Скопируй сейчас — больше мы его не покажем.
              </DialogDescription>
            </DialogHeader>

            <div className="mt-4 flex flex-col gap-3">
              <div className="rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-body-sm break-all text-gray-900">
                {createdToken.full_token}
              </div>
              <Button
                type="button"
                variant="primary"
                onClick={copyToken}
                leftIcon={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                data-testid="copy-full-mcp-token"
              >
                {copied ? 'Скопировано' : 'Скопировать токен'}
              </Button>
              <Banner
                variant="warning"
                title="Это последний раз, когда ты видишь полный токен"
                description="Если потеряешь — придётся создавать заново. В UI остаётся только префикс."
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="secondary"
                onClick={() => onOpenChange(false)}
                data-testid="create-mcp-token-done"
              >
                Готово
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form noValidate onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
            <DialogHeader>
              <DialogTitle>Новый MCP-токен</DialogTitle>
              <DialogDescription>
                Используется в Claude Desktop, Cursor, Continue, Zed. Имя видно только тебе.
              </DialogDescription>
            </DialogHeader>

            <Field>
              <Label htmlFor="mcp-token-name">Имя токена</Label>
              <Input
                id="mcp-token-name"
                placeholder="Claude Desktop (macbook)"
                autoFocus
                invalid={Boolean(errors.name)}
                aria-describedby="mcp-token-name-help mcp-token-name-error"
                {...register('name')}
              />
              <p id="mcp-token-name-help" className="text-body-sm text-gray-500">
                Помогает отличать токены между собой. Совет: укажи устройство.
              </p>
              <FieldError id="mcp-token-name-error">{errors.name?.message}</FieldError>
            </Field>

            <Field>
              <Label htmlFor="mcp-token-scope">Доступ</Label>
              <select
                id="mcp-token-scope"
                className="h-10 rounded-md border border-gray-300 bg-white px-3 text-body text-gray-900 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
                aria-describedby="mcp-token-scope-help"
                {...register('scope')}
              >
                <option value="all">Все 7 tools (рекомендуется)</option>
                <option value="read_account">Только чтение аккаунта</option>
                <option value="read_usage">Только чтение usage</option>
                <option value="recommend_model">Только рекомендация модели</option>
                <option value="list_models">Только каталог моделей</option>
                <option value="read_traces">Только последние traces</option>
                <option value="list_cookbook">Только cookbook-рецепты</option>
                <option value="list_integrations">Только список интеграций</option>
              </select>
              <p id="mcp-token-scope-help" className="text-body-sm text-gray-500">
                {SCOPE_HELP[selectedScope]}
              </p>
            </Field>

            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
                Отмена
              </Button>
              <Button
                type="submit"
                loading={isSubmitting || create.isPending}
                data-testid="create-mcp-token-submit"
              >
                Создать токен
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
