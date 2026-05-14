'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { authApi, ApiClientError, type OAuthIdentity } from '@/lib/api';
import { toast } from '@/components/ui/toast';

/**
 * Settings → Безопасность → Привязанные аккаунты.
 *
 * Показывает Google + Yandex с пометкой «связано» / «не связано». «Связать»
 * — это `<a href="/v1/auth/oauth/{provider}/start?mode=link">` (server
 * redirect, без fetch на клиенте). «Отвязать» — POST /disconnect; backend
 * 400 если это единственный способ логина — мы это переиграем как тоаст со
 * ссылкой на «задай пароль».
 *
 * UX: одна Card на оба провайдера, чтобы не плодить однотипные карточки.
 */

const PROVIDERS: { key: 'google' | 'yandex'; label: string }[] = [
  { key: 'google', label: 'Google' },
  { key: 'yandex', label: 'Яндекс' },
];

export function LinkedAccountsCard() {
  const qc = useQueryClient();
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

  const list = useQuery({
    queryKey: ['oauth-identities'],
    queryFn: () => authApi.listOAuthIdentities(),
  });

  const disconnect = useMutation({
    mutationFn: (provider: 'google' | 'yandex') => authApi.disconnectOAuth(provider),
    onSuccess: (_data, provider) => {
      toast.success(
        `${provider === 'google' ? 'Google' : 'Яндекс'} отвязан.`,
      );
      qc.invalidateQueries({ queryKey: ['oauth-identities'] });
    },
    onError: (err) => {
      if (err instanceof ApiClientError && err.code === 'last_login_method') {
        toast.error(
          'Это единственный способ входа. Сначала задай пароль через «Сменить пароль».',
        );
        return;
      }
      const message = err instanceof ApiClientError ? err.message : 'Не удалось отвязать.';
      toast.error(message);
    },
  });

  const linked = (list.data?.identities ?? []) as OAuthIdentity[];
  const isLoading = list.isLoading;

  return (
    <Card>
      <CardTitle>Привязанные аккаунты</CardTitle>
      <CardDescription className="mt-1">
        Войти можно через Google или Яндекс — кроме email и пароля.
      </CardDescription>

      <div className="mt-4 flex flex-col gap-3">
        {PROVIDERS.map(({ key, label }) => {
          const ident = linked.find((i) => i.provider === key);
          const startUrl = `${apiBase}/v1/auth/oauth/${key}/start?mode=link`;
          return (
            <div
              key={key}
              className="flex items-center justify-between rounded-md border p-3"
              style={{ borderColor: 'var(--hairline)' }}
              data-testid={`linked-${key}-row`}
            >
              <div className="flex flex-col">
                <span style={{ fontWeight: 600 }}>{label}</span>
                {ident ? (
                  <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
                    {ident.email_at_link ?? 'без email'}
                    {ident.last_login_at ? (
                      <>
                        {' · '}
                        вход{' '}
                        {new Intl.DateTimeFormat('ru-RU', {
                          day: '2-digit',
                          month: 'short',
                          year: 'numeric',
                        }).format(new Date(ident.last_login_at))}
                      </>
                    ) : null}
                  </span>
                ) : (
                  <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>не привязан</span>
                )}
              </div>

              {ident ? (
                <Button
                  variant="secondary"
                  loading={disconnect.isPending && disconnect.variables === key}
                  onClick={() => disconnect.mutate(key)}
                  data-testid={`disconnect-${key}`}
                >
                  Отвязать
                </Button>
              ) : (
                <a
                  href={startUrl}
                  className="brikko-btn brikko-btn-secondary"
                  style={{ textDecoration: 'none' }}
                  data-testid={`connect-${key}`}
                >
                  Привязать
                </a>
              )}
            </div>
          );
        })}

        {isLoading ? (
          <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Загружаем…</div>
        ) : null}
      </div>
    </Card>
  );
}
