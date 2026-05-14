'use client';

import { useState } from 'react';
import { ShieldCheck, ShieldOff } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { TwoFactorSetupModal } from './TwoFactorSetupModal';
import { TwoFactorDisableModal } from './TwoFactorDisableModal';
import { useAccount } from '@/lib/auth';
import { formatDate } from '@/lib/utils';

/**
 * 2FA-карточка в Security tab.
 *
 * UX: enabled-state — зелёный statusband + кнопка «Отключить» как secondary destructive,
 * disabled-state — primary CTA «Включить». Описание разное в обоих состояниях, потому
 * что мотивация юзера тоже разная: «защити», когда выключено / «когда включил» когда уже работает.
 */

export function TwoFactorCard() {
  const account = useAccount();
  const [setupOpen, setSetupOpen] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);

  const enabled = Boolean(account.data?.two_factor_enabled);
  const enabledAt = account.data?.two_factor_enabled_at;

  return (
    <>
      <Card>
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle className="flex items-center gap-2">
              {enabled ? (
                <ShieldCheck className="h-5 w-5 text-success-600" strokeWidth={1.75} aria-hidden="true" />
              ) : (
                <ShieldOff className="h-5 w-5 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
              )}
              Двухфакторная аутентификация
            </CardTitle>
            <CardDescription>
              {enabled
                ? 'При входе после пароля попросим 6-значный код из Google Authenticator / Authy / 1Password.'
                : 'Защита от утечки пароля. Код из приложения телефона нужен будет на каждом входе.'}
            </CardDescription>
          </div>
          {enabled ? <Badge variant="success">Включена</Badge> : null}
        </div>

        {enabled && enabledAt ? (
          <p className="mt-3 text-body-sm text-gray-500">
            Включена {formatDate(enabledAt)}
          </p>
        ) : null}

        <div className="mt-5">
          {enabled ? (
            <Button
              variant="secondary"
              onClick={() => setDisableOpen(true)}
              data-testid="2fa-disable-button"
            >
              Отключить 2FA
            </Button>
          ) : (
            <Button onClick={() => setSetupOpen(true)} data-testid="2fa-enable-button">
              Включить 2FA
            </Button>
          )}
        </div>
      </Card>

      <TwoFactorSetupModal
        open={setupOpen}
        onOpenChange={setSetupOpen}
        onSuccess={() => {
          // useAccount invalidate стоит в hook'е — UI пере-fetch'нет автоматом.
        }}
      />
      <TwoFactorDisableModal
        open={disableOpen}
        onOpenChange={setDisableOpen}
        onSuccess={() => {
          // Аналогично.
        }}
      />
    </>
  );
}
