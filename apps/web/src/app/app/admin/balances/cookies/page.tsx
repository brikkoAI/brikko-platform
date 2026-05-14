'use client';

/**
 * Provider cookies admin page — Phase 2 (2026-05-11).
 *
 * CEO logs into OpenAI / Anthropic / Together dashboards in his real browser,
 * exports cookies as JSON (Playwright `context.cookies()` dump or a browser
 * extension like "Get cookies.txt LOCALLY"), and pastes the JSON here.
 *
 * Backend Fernet-encrypts via ENCRYPTION_KEY and forwards to brikko-scraper,
 * which stores the blob on a shared volume. The Playwright scraper picks it
 * up on next refresh.
 *
 * Why a textarea (not a file input):
 *   - 100% of the export tooling produces a string the user can copy.
 *   - File picker adds a step + risks the user picking a binary by mistake.
 *   - We validate JSON on submit; clear error if format wrong.
 *
 * Access: ADMIN_EMAILS only (server enforces 403).
 */

import { useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Cookie,
  ExternalLink,
  Trash2,
  Upload,
  XCircle,
} from 'lucide-react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  type ApiClientError,
  type ProviderCookieMeta,
  type ScrapeProviderKey,
  deleteProviderCookies,
  fetchProviderCookies,
  type GetProviderCookiesResponse,
  uploadProviderCookies,
} from '@/lib/api';
import { formatRelative } from '@/lib/utils';

const QK_COOKIES = ['admin-provider-cookies'] as const;

const PROVIDER_META: Record<
  ScrapeProviderKey,
  { name: string; loginUrl: string; exportHint: string }
> = {
  openai: {
    name: 'OpenAI',
    loginUrl: 'https://platform.openai.com/account/billing/overview',
    exportHint:
      'Войди в platform.openai.com, открой DevTools → Application → Cookies, экспортируй platform.openai.com через Playwright context.cookies() или расширение «Get cookies.txt LOCALLY» в формате JSON.',
  },
  anthropic: {
    name: 'Anthropic',
    loginUrl: 'https://console.anthropic.com/settings/billing',
    exportHint:
      'Войди в console.anthropic.com, экспортируй cookies для домена console.anthropic.com (и связанных) в JSON-формате.',
  },
  together: {
    name: 'Together.ai',
    loginUrl: 'https://api.together.xyz/settings/billing',
    exportHint:
      'Войди в api.together.xyz / api.together.ai, экспортируй cookies в JSON.',
  },
};

const PROVIDER_ORDER: ScrapeProviderKey[] = ['openai', 'anthropic', 'together'];

export default function AdminCookiesPage() {
  const qc = useQueryClient();
  const [editingProvider, setEditingProvider] =
    useState<ScrapeProviderKey | null>(null);

  const cookies = useQuery({
    queryKey: QK_COOKIES,
    queryFn: fetchProviderCookies,
    staleTime: 30_000,
  });

  if (cookies.isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader />
        <Card className="p-0">
          <Skeleton className="h-12 w-full rounded-none" />
          <div className="flex flex-col gap-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        </Card>
      </div>
    );
  }

  if (cookies.isError) {
    const err = cookies.error as ApiClientError;
    if (err.status === 403) {
      return (
        <Card className="p-12 text-center">
          <XCircle
            className="mx-auto mb-4 h-12 w-12 text-red-500"
            aria-hidden="true"
          />
          <CardTitle className="mb-2 text-h3">Доступ запрещён</CardTitle>
          <CardDescription className="text-body text-fg-muted">
            Только администраторы платформы (ADMIN_EMAILS) могут управлять
            cookies для скрейпера.
          </CardDescription>
        </Card>
      );
    }
    return (
      <Card className="p-8 text-center">
        <AlertTriangle
          className="mx-auto mb-4 h-10 w-10 text-amber-500"
          aria-hidden="true"
        />
        <CardTitle className="mb-2 text-h4">
          Не удалось загрузить cookies
        </CardTitle>
        <CardDescription>{err.message ?? 'Неизвестная ошибка'}</CardDescription>
      </Card>
    );
  }

  const items = cookies.data?.items ?? [];
  const byProvider = new Map(items.map((item) => [item.provider, item]));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader />

      <div className="flex flex-col gap-3">
        {PROVIDER_ORDER.map((provider) => {
          const meta = byProvider.get(provider) ?? {
            provider,
            uploaded_at: null,
            last_valid_at: null,
            last_error: null,
            size_bytes: null,
            has_cookies: false,
          };
          return (
            <CookieCard
              key={provider}
              provider={provider}
              meta={meta}
              isEditing={editingProvider === provider}
              onEditStart={() => setEditingProvider(provider)}
              onEditCancel={() => setEditingProvider(null)}
              onEditSaved={() => {
                setEditingProvider(null);
                void qc.invalidateQueries({ queryKey: QK_COOKIES });
              }}
            />
          );
        })}
      </div>

      <Card className="bg-amber-50 p-4 text-body-sm text-amber-700">
        <strong>Безопасность:</strong> cookies дают полный доступ к dashboard
        провайдера. Они шифруются Fernet&apos;ом перед сохранением, но всё ещё
        чувствительны. Не сохраняй их в публичных репозиториях / Notion и не
        пересылай в Telegram. При компрометации — logout в браузере провайдера
        отзывает session сервер-сайдово, после этого можно загрузить новые.
      </Card>
    </div>
  );
}

function PageHeader() {
  return (
    <header className="flex flex-col gap-3">
      <span className="inline-flex items-center gap-2 text-body-sm text-fg-muted">
        <Cookie className="h-4 w-4" aria-hidden="true" />
        Admin · Provider cookies
      </span>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="text-h2 font-semibold tracking-tight text-fg-primary">
            Cookies для скрейпера балансов
          </h1>
          <p className="max-w-2xl text-body text-fg-muted">
            OpenAI / Anthropic / Together не дают API для баланса. Playwright
            scraper логинится через cookies, выгруженные из твоего браузера.
          </p>
        </div>
        <Link href="/app/admin/balances">
          <Button
            variant="secondary"
            leftIcon={<ArrowLeft className="h-4 w-4" />}
          >
            К балансам
          </Button>
        </Link>
      </div>
    </header>
  );
}

interface CookieCardProps {
  provider: ScrapeProviderKey;
  meta: ProviderCookieMeta;
  isEditing: boolean;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSaved: () => void;
}

function CookieCard({
  provider,
  meta,
  isEditing,
  onEditStart,
  onEditCancel,
  onEditSaved,
}: CookieCardProps) {
  const m = PROVIDER_META[provider];
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-h4">{m.name}</CardTitle>
          <CardDescription className="text-body-sm">
            {meta.has_cookies ? (
              <>
                Cookies загружены{' '}
                {meta.uploaded_at ? formatRelative(meta.uploaded_at) : 'недавно'}
                {meta.size_bytes ? ` · ${meta.size_bytes} bytes` : ''}
                {meta.last_valid_at ? (
                  <>
                    {' · '}последний успешный scrape:{' '}
                    {formatRelative(meta.last_valid_at)}
                  </>
                ) : null}
              </>
            ) : (
              'Cookies ещё не загружены — scraper будет возвращать ошибку.'
            )}
          </CardDescription>
          {meta.last_error ? (
            <CardDescription className="text-body-sm text-red-600">
              Последняя ошибка: {meta.last_error}
            </CardDescription>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {meta.has_cookies ? (
            <Badge variant="success">загружены</Badge>
          ) : (
            <Badge variant="neutral">пусто</Badge>
          )}
          <a
            href={m.loginUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 items-center gap-1 rounded-md border border-gray-300 px-3 text-body-sm font-medium text-fg-muted transition-colors hover:bg-gray-50 hover:text-fg-primary"
          >
            Открыть dashboard
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
          {!isEditing ? (
            <Button
              size="sm"
              onClick={onEditStart}
              leftIcon={<Upload className="h-3.5 w-3.5" />}
            >
              {meta.has_cookies ? 'Заменить' : 'Загрузить'}
            </Button>
          ) : null}
          {meta.has_cookies && !isEditing ? (
            <DeleteButton provider={provider} onDeleted={onEditSaved} />
          ) : null}
        </div>
      </div>

      {isEditing ? (
        <UploadForm
          provider={provider}
          exportHint={m.exportHint}
          onCancel={onEditCancel}
          onSaved={onEditSaved}
        />
      ) : null}
    </Card>
  );
}

function UploadForm({
  provider,
  exportHint,
  onCancel,
  onSaved,
}: {
  provider: ScrapeProviderKey;
  exportHint: string;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [json, setJson] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (payload: string) => uploadProviderCookies(provider, payload),
    onSuccess: () => onSaved(),
  });

  const error = save.error as ApiClientError | null;

  const validate = (raw: string): string | null => {
    if (!raw.trim()) return 'Пустое поле';
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return 'Ожидается JSON-массив объектов';
      if (parsed.length === 0) return 'Массив пустой';
      for (let i = 0; i < parsed.length; i++) {
        const item = parsed[i];
        if (typeof item !== 'object' || item === null) {
          return `Элемент [${i}] не объект`;
        }
        if (!('name' in item) || !('value' in item)) {
          return `Элемент [${i}] должен содержать поля name и value`;
        }
      }
      return null;
    } catch (err) {
      return `Невалидный JSON: ${(err as Error).message}`;
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const err = validate(json);
    setJsonError(err);
    if (err) return;
    save.mutate(json);
  };

  return (
    <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3">
      <p className="text-body-sm text-fg-muted">{exportHint}</p>
      <label
        htmlFor={`cookies-${provider}`}
        className="text-body-sm font-medium text-fg-primary"
      >
        Cookies JSON
      </label>
      <textarea
        id={`cookies-${provider}`}
        value={json}
        onChange={(e) => {
          setJson(e.target.value);
          if (jsonError) setJsonError(validate(e.target.value));
        }}
        rows={8}
        className="min-h-[160px] w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-body-sm text-gray-900 hover:border-gray-400 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
        placeholder='[{"name": "__Secure-...", "value": "...", "domain": "platform.openai.com", "path": "/"}]'
        spellCheck={false}
        autoComplete="off"
      />
      {jsonError ? (
        <div role="alert" className="text-body-sm text-red-600">
          {jsonError}
        </div>
      ) : null}
      {error ? (
        <div role="alert" className="text-body-sm text-red-600">
          Backend: {error.message}
        </div>
      ) : null}
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          loading={save.isPending}
          disabled={!json.trim() || !!jsonError}
          leftIcon={<Check className="h-3.5 w-3.5" />}
        >
          Загрузить
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onCancel}
          disabled={save.isPending}
        >
          Отмена
        </Button>
      </div>
    </form>
  );
}

function DeleteButton({
  provider,
  onDeleted,
}: {
  provider: ScrapeProviderKey;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const del = useMutation({
    mutationFn: () => deleteProviderCookies(provider),
    onSuccess: () => {
      setConfirming(false);
      onDeleted();
    },
  });
  if (!confirming) {
    return (
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setConfirming(true)}
        aria-label={`Удалить cookies ${provider}`}
        leftIcon={<Trash2 className="h-3.5 w-3.5" />}
      >
        Удалить
      </Button>
    );
  }
  return (
    <div className="flex gap-1">
      <Button
        size="sm"
        variant="ghost"
        onClick={() => del.mutate()}
        loading={del.isPending}
      >
        Точно удалить?
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setConfirming(false)}
        disabled={del.isPending}
      >
        Нет
      </Button>
    </div>
  );
}
