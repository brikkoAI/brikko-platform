'use client';

import { useEffect, useState } from 'react';
import { Copy, Check, Download } from 'lucide-react';
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
import { Banner } from '@/components/ui/banner';
import { useTwoFactorSetup, useTwoFactorVerify } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';
import type { TwoFactorSetupResponse } from '@/lib/types';

/**
 * 3-шаговый wizard включения 2FA в одной модалке.
 *
 * UX-обоснование:
 *  - 3 шага в ОДНОЙ модалке, не 3 разных — потому что: (1) пользователь не должен потерять
 *    контекст между «вот секрет» и «введи код», (2) recovery-codes ОБЯЗАТЕЛЬНЫ к сохранению,
 *    разделение по разным экранам провоцирует «закрыл — забыл сохранить».
 *  - Чекбокс «Я сохранил коды» — UX-pattern из Stripe / 1Password / Vercel: блокирует
 *    закрытие шага 3 до явного подтверждения. Не «кнопка disabled навсегда», а конкретный
 *    contract: «ты подтверждаешь, что коды у тебя».
 *  - QR-code мы НЕ генерируем сами через canvas-lib — слишком много lib'ы за +20 КБ.
 *    Показываем otpauth:// URL + secret. Пользователю Google Authenticator predominantly
 *    предложит «вписать вручную», что покрыто полем Secret. Большинство 2FA-apps умеют
 *    парсить otpauth:// link из clipboard (Authy / 1Password). Это компромисс.
 */

interface TwoFactorSetupModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
}

type Step = 1 | 2 | 3;

export function TwoFactorSetupModal({ open, onOpenChange, onSuccess }: TwoFactorSetupModalProps) {
  const setup = useTwoFactorSetup();
  const verify = useTwoFactorVerify();
  const [step, setStep] = useState<Step>(1);
  const [setupData, setSetupData] = useState<TwoFactorSetupResponse | null>(null);
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [savedCodesAck, setSavedCodesAck] = useState(false);
  const [secretCopied, setSecretCopied] = useState(false);

  // Сброс при закрытии — чтобы при повторном открытии не было «следы прошлого setup».
  useEffect(() => {
    if (!open) {
      setStep(1);
      setSetupData(null);
      setCode('');
      setCodeError(null);
      setSavedCodesAck(false);
      setSecretCopied(false);
    }
  }, [open]);

  // На открытии модалки — сразу запрашиваем secret + recovery codes.
  // Это backend-state «pending2fa», который активируется на verify (шаг 2).
  useEffect(() => {
    if (!open || setupData || setup.isPending) return;
    setup
      .mutateAsync()
      .then(setSetupData)
      .catch((err) => {
        const message =
          err instanceof ApiClientError ? err.message : 'Не удалось запустить 2FA setup.';
        toast.error(message);
        onOpenChange(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function handleVerify() {
    if (!/^\d{6}$/.test(code)) {
      setCodeError('Введи 6-значный код');
      return;
    }
    setCodeError(null);
    try {
      await verify.mutateAsync(code);
      setStep(3);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'invalid_credentials') {
        setCodeError('Неверный код. Проверь время на телефоне и попробуй снова.');
        return;
      }
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось подтвердить код.';
      toast.error(message);
    }
  }

  async function copySecret() {
    if (!setupData) return;
    try {
      await navigator.clipboard.writeText(setupData.secret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 1500);
    } catch {
      toast.error('Не удалось скопировать. Выдели вручную.');
    }
  }

  function downloadRecoveryCodes() {
    if (!setupData) return;
    const text = [
      'Brikko — recovery-коды для 2FA',
      `Дата выдачи: ${new Date().toISOString()}`,
      '',
      'Эти коды — твой fallback, если потерял телефон. Каждый код одноразовый.',
      'Храни их в password-manager или распечатай и положи в сейф.',
      '',
      ...setupData.recovery_codes,
    ].join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'brikko-recovery-codes.txt';
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // На шаге 3 закрытие разрешено только после подтверждения «коды сохранены»,
        // иначе пользователь рискует потерять recovery codes навсегда.
        if (!next && step === 3 && !savedCodesAck) {
          toast.warning('Подтверди что сохранил recovery-коды — без них восстановить доступ нельзя.');
          return;
        }
        onOpenChange(next);
      }}
    >
      <DialogContent
        className="max-w-lg"
        data-testid="2fa-setup-modal"
        aria-describedby="2fa-step-desc"
      >
        <DialogHeader>
          <DialogTitle>
            {step === 1
              ? 'Шаг 1 из 3 — сканируй QR'
              : step === 2
                ? 'Шаг 2 из 3 — введи код'
                : 'Шаг 3 из 3 — сохрани recovery-коды'}
          </DialogTitle>
          <DialogDescription id="2fa-step-desc">
            {step === 1
              ? 'Открой Google Authenticator, Authy или 1Password и добавь новый аккаунт.'
              : step === 2
                ? 'Введи 6-значный код, который показывает приложение для аккаунта Brikko.'
                : 'Это твой fallback на случай потери телефона. Сохрани в password-manager.'}
          </DialogDescription>
        </DialogHeader>

        {step === 1 ? (
          setupData ? (
            <div className="mt-4 flex flex-col gap-4">
              <div>
                <Label htmlFor="2fa-secret">Secret (для ручного ввода)</Label>
                <div className="mt-1.5 flex items-stretch gap-2">
                  <code
                    id="2fa-secret"
                    className="flex-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-mono text-gray-800"
                    data-testid="2fa-secret"
                  >
                    {setupData.secret}
                  </code>
                  <Button
                    variant="secondary"
                    size="sm"
                    leftIcon={
                      secretCopied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />
                    }
                    onClick={copySecret}
                  >
                    {secretCopied ? 'Скопировано' : 'Копировать'}
                  </Button>
                </div>
                <FieldHelper id="2fa-secret-help" className="mt-1.5">
                  В большинстве 2FA-приложений: «Добавить аккаунт» → «Ввести ключ настройки».
                </FieldHelper>
              </div>

              <details className="rounded-md border border-gray-200 bg-gray-50 p-3 text-body-sm">
                <summary className="cursor-pointer font-medium text-gray-700">
                  Или otpauth:// link для парсинга
                </summary>
                <code
                  className="mt-2 block break-all rounded bg-white p-2 text-xs font-mono text-gray-700"
                  data-testid="2fa-otpauth-url"
                >
                  {setupData.qr_code_url}
                </code>
              </details>
            </div>
          ) : (
            <div className="mt-4 h-32 animate-pulse rounded-md bg-gray-100" />
          )
        ) : null}

        {step === 2 ? (
          <Field className="mt-4">
            <Label htmlFor="2fa-code">6-значный код</Label>
            <Input
              id="2fa-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              pattern="\d{6}"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              invalid={Boolean(codeError)}
              aria-describedby="2fa-code-error 2fa-code-help"
              data-testid="2fa-code-input"
            />
            <FieldHelper id="2fa-code-help">
              Код обновляется каждые 30 секунд — введи самый свежий.
            </FieldHelper>
            <FieldError id="2fa-code-error">{codeError ?? undefined}</FieldError>
          </Field>
        ) : null}

        {step === 3 && setupData ? (
          <div className="mt-4 flex flex-col gap-4">
            <Banner
              variant="success"
              title="2FA включена"
              description="Теперь при входе после пароля попросим код из приложения."
            />
            <div>
              <Label>Recovery-коды (8 шт.)</Label>
              <ul
                className="mt-1.5 grid grid-cols-2 gap-1.5 rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-sm"
                data-testid="2fa-recovery-codes"
              >
                {setupData.recovery_codes.map((c) => (
                  <li key={c} className="text-gray-800">
                    {c}
                  </li>
                ))}
              </ul>
              <Button
                variant="secondary"
                size="sm"
                className="mt-3"
                leftIcon={<Download className="h-4 w-4" />}
                onClick={downloadRecoveryCodes}
              >
                Скачать как .txt
              </Button>
            </div>

            <label className="flex items-start gap-3 rounded-md border border-warning-200 bg-warning-50 p-3">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                checked={savedCodesAck}
                onChange={(e) => setSavedCodesAck(e.target.checked)}
                data-testid="2fa-saved-ack"
              />
              <span className="text-body-sm text-gray-900">
                Я сохранил recovery-коды в надёжном месте. Понимаю, что без них при потере
                телефона восстановить доступ невозможно.
              </span>
            </label>
          </div>
        ) : null}

        <DialogFooter>
          {step === 1 ? (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Отмена
              </Button>
              <Button onClick={() => setStep(2)} disabled={!setupData}>
                Дальше
              </Button>
            </>
          ) : null}

          {step === 2 ? (
            <>
              <Button variant="ghost" onClick={() => setStep(1)}>
                Назад
              </Button>
              <Button onClick={handleVerify} loading={verify.isPending} data-testid="2fa-verify-cta">
                Подтвердить
              </Button>
            </>
          ) : null}

          {step === 3 ? (
            <Button
              onClick={() => {
                onSuccess();
                onOpenChange(false);
              }}
              disabled={!savedCodesAck}
              data-testid="2fa-finish-cta"
            >
              Готово
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
